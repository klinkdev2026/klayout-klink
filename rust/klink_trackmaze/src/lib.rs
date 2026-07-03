// klink_trackmaze_rs -- Rust TrackGrid maze kernel for klink Track 2.
//
// SEPARATE from Track-1's klink_boxmaze (do NOT touch that crate). Faithful port of the
// Track-2 TrackMaze initial routing (klink/routing/backends/pnr_multilayer/dr/maze.py:
// route_all's per-net A*): flat-array occupancy + _astar/_neighbors/_foreign with the SAME
// portals / via legality / spacing halo / cost model. The Python path stays the fallback;
// this must be byte-parity (or at least LVS-clean) against it before any semantic change.
//
// Node addressing follows FlexGridGraph: flat maze-index with the row/col-major flip per
// layer direction (H: x+y*nx; V: y+x*ny; +z*nx*ny). Planar neighbours are +-stride.
#![allow(clippy::too_many_arguments)]
use pyo3::prelude::*;
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet};

const BLOCK: u8 = 255;

struct Grid<'a> {
    nx: i32,
    ny: i32,
    nz: i32,
    xy: i32,
    z_is_h: Vec<bool>,
    via_lo: HashSet<i32>, // lower-z of each adjacent via pair (z,z+1)
    edge_e: &'a [u8],
    edge_n: &'a [u8],
    blk_e: &'a [u8],
    blk_n: &'a [u8],
    fsc_h: &'a [u8],
    fsc_v: &'a [u8],
    fsc_via: &'a [u8],
    mc: &'a [u8],
}

impl<'a> Grid<'a> {
    #[inline(always)]
    fn get_idx(&self, xi: i32, yi: i32, zi: i32) -> i32 {
        if self.z_is_h[zi as usize] {
            xi + yi * self.nx + zi * self.xy
        } else {
            yi + xi * self.ny + zi * self.xy
        }
    }
    #[inline(always)]
    fn coords(&self, idx: i32) -> (i32, i32, i32) {   // reverse get_idx -> (xi, yi, zi)
        let zi = idx / self.xy;
        let planar = idx % self.xy;
        if self.z_is_h[zi as usize] {
            (planar % self.nx, planar / self.nx, zi)
        } else {
            (planar / self.ny, planar % self.ny, zi)
        }
    }
}

// A* heap entry: ascending (f, cost, (xi,yi,zi)). Wrapped in Reverse via BinaryHeap by
// flipping Ord so the smallest pops first (matches Python heapq min-pop + tuple tie-break).
// `ord` is an xi-MAJOR key so the tie-break is lexicographic (xi,yi,zi) -- EXACTLY Python's
// node-tuple comparison (the packed `node` i64 is zi-major and must NOT order the heap).
#[derive(PartialEq)]
struct HeapItem {
    f: f64,
    cost: f64,
    ord: i64,  // xi-major: (xi<<40)|(yi<<20)|zi -> lexicographic (xi,yi,zi)
    node: i64, // packed xi|yi<<16|zi<<32 (identity)
    nidx: i32, // flat maze-index (key into the scratch arrays)
}
impl Eq for HeapItem {}
impl Ord for HeapItem {
    fn cmp(&self, o: &Self) -> Ordering {
        // reverse: smaller f/cost/ord is "greater" so BinaryHeap (max-heap) pops it first
        o.f.partial_cmp(&self.f)
            .unwrap_or(Ordering::Equal)
            .then_with(|| o.cost.partial_cmp(&self.cost).unwrap_or(Ordering::Equal))
            .then_with(|| o.ord.cmp(&self.ord))
    }
}
impl PartialOrd for HeapItem {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}

#[inline(always)]
fn pack(xi: i32, yi: i32, zi: i32) -> i64 {
    (xi as i64) | ((yi as i64) << 16) | ((zi as i64) << 32)
}
#[inline(always)]
fn unpack(n: i64) -> (i32, i32, i32) {
    ((n & 0xFFFF) as i32, ((n >> 16) & 0xFFFF) as i32, (n >> 32) as i32)
}
#[inline(always)]
fn xy_key(xi: i32, yi: i32) -> i64 {
    (xi as i64) | ((yi as i64) << 16)
}
#[inline(always)]
fn ord_key(xi: i32, yi: i32, zi: i32) -> i64 {
    ((xi as i64) << 40) | ((yi as i64) << 20) | (zi as i64) // lexicographic (xi,yi,zi)
}

struct Maze<'a> {
    g: Grid<'a>,
    occ: Vec<i32>, // owner net_id per flat idx; -1 empty, -2 multiple
    pad_owner: &'a HashMap<i32, i32>, // flat idx -> owner net_id (legality)
    marker_weight: f64,
    occ_penalty: f64,
    via_cost: f64,
    jog_cost: f64,
    spacing_halo: i32,
    sig: Option<HashSet<i32>>, // planar layers (None = all)
    no_jog: HashSet<i32>,
    marker: Vec<i32>,          // accumulated DRC/overlap steering cost per flat idx (mutable)
    multi: HashMap<i32, Vec<i32>>, // flat idx -> nets, ONLY for overlapped cells (occ == -2)
    routes: HashMap<i32, Vec<i64>>,       // net -> committed nodes (for worker-box rip-up)
    edges: HashMap<i32, Vec<(i64, i64)>>, // net -> actual routed edges (worker-box topology)
    // T4 track-balancing: per (signal layer, perpendicular coord) net-usage count, so the
    // initial A* prefers LESS-used tracks -> nets spread onto distinct tracks (FlexTA effect,
    // integrated) -> far fewer overlaps born. key = (z<<32)|perp. weight 0 disables.
    track_use: HashMap<i64, i32>,
    track_weight: f64,
}

// Reusable A* scratch: flat arrays keyed by the maze flat-index (no HashMap, no per-call
// clear -- a generation stamp marks which entries belong to the current search).
struct Scratch {
    best: Vec<f64>,
    came: Vec<i64>,
    gen_best: Vec<u32>,
    gen_came: Vec<u32>,
    gen: u32,
    heap: BinaryHeap<HeapItem>,
}
impl Scratch {
    fn new(cap: usize) -> Self {
        Scratch {
            best: vec![0.0; cap],
            came: vec![0i64; cap],
            gen_best: vec![0u32; cap],
            gen_came: vec![0u32; cap],
            gen: 0,
            heap: BinaryHeap::new(),
        }
    }
    #[inline(always)]
    fn best_of(&self, i: usize) -> f64 {
        if self.gen_best[i] == self.gen { self.best[i] } else { f64::INFINITY }
    }
}

impl<'a> Maze<'a> {
    // foreign: a net OTHER than `net` occupies node (or its perpendicular halo band)
    #[inline]
    fn foreign(&self, xi: i32, yi: i32, zi: i32, idx: i32, net: i32) -> bool {
        let h = self.spacing_halo;
        if h == 0 {
            let o = self.occ[idx as usize];
            return o != -1 && o != net;
        }
        let perp_y = self.g.z_is_h[zi as usize];
        for d in -h..=h {
            let (cx, cy) = if perp_y { (xi, yi + d) } else { (xi + d, yi) };
            if cx < 0 || cy < 0 || cx >= self.g.nx || cy >= self.g.ny {
                continue;
            }
            let ci = self.g.get_idx(cx, cy, zi);
            let o = self.occ[ci as usize];
            if o != -1 && o != net {
                return true;
            }
        }
        false
    }

    #[inline]
    fn wire_ok_idx(&self, idx: i32, xi: i32, yi: i32, zi: i32, net: i32) -> bool {
        if self.g.fsc_h[idx as usize] >= BLOCK || self.g.fsc_v[idx as usize] >= BLOCK {
            match self.pad_owner.get(&idx) {
                Some(&o) => o == net,
                None => false,
            }
        } else {
            true
        }
    }

    // track cost: planar move onto a less-used signal track is cheaper -> nets spread (T4).
    #[inline(always)]
    fn track_cost(&self, x: i32, y: i32, z: i32) -> f64 {
        if self.track_weight == 0.0 || self.sig.as_ref().map_or(true, |s| !s.contains(&z)) {
            return 0.0;
        }
        let perp = if self.g.z_is_h[z as usize] { y } else { x };
        let key = ((z as i64) << 32) | (perp as i64);
        (*self.track_use.get(&key).unwrap_or(&0) as f64) * self.track_weight
    }

    // relax one neighbour into the scratch (flat-array best/came + generation stamp).
    #[inline(always)]
    fn relax(&self, sc: &mut Scratch, node: i64, cost: f64, nb: i64, nb_idx: i32,
             nx2: i32, ny2: i32, nz2: i32, step: f64, dx: i32, dy: i32, dz: i32,
             net: i32, hard_avoid: bool, track_extra: f64) {
        let mc = self.g.mc[nb_idx as usize] as i32 + self.marker[nb_idx as usize];
        let mut ext = (mc as f64) * self.marker_weight + track_extra;
        if self.foreign(nx2, ny2, nz2, nb_idx, net) {
            if hard_avoid {
                return;
            }
            ext += self.occ_penalty;
        }
        let nc = cost + step + ext;
        let ui = nb_idx as usize;
        if nc + 1e-12 < sc.best_of(ui) {
            sc.best[ui] = nc;
            sc.gen_best[ui] = sc.gen;
            sc.came[ui] = node;
            sc.gen_came[ui] = sc.gen;
            let hh = ((nx2 - dx).abs() + (ny2 - dy).abs() + (nz2 - dz).abs()) as f64;
            sc.heap.push(HeapItem { f: nc + hh, cost: nc, ord: ord_key(nx2, ny2, nz2),
                                    node: nb, nidx: nb_idx });
        }
    }

    // A* from a set of source nodes to dest. Returns the node path (packed) or None.
    fn astar(
        &self,
        sc: &mut Scratch,
        net: i32,
        sources: &HashSet<i64>,
        dest: i64,
        win: &HashSet<i64>,
        portals: &HashSet<i64>,
        corridor: Option<&HashSet<i64>>,   // T3 guide gcell band (planar move must stay inside)
        gc: i32,
        bx: Option<(i32, i32, i32, i32)>,  // local cell box (x0,y0,x1,y1) for box-local rip-up
        hard_avoid: bool,
        expanded: &mut u64,
    ) -> Option<Vec<i64>> {
        let g = &self.g;
        let (dx, dy, dz) = unpack(dest);
        let dest_idx = g.get_idx(dx, dy, dz) as usize;
        let in_corr = |x: i32, y: i32| -> bool {
            (match corridor {
                None => true,
                Some(c) => c.contains(&(((x / gc) as i64) | (((y / gc) as i64) << 32))),
            }) && match bx {
                None => true,
                Some((x0, y0, x1, y1)) => x >= x0 && x <= x1 && y >= y0 && y <= y1,
            }
        };
        sc.gen += 1;
        let gen = sc.gen;
        sc.heap.clear();
        for &s in sources {
            let (sx, sy, sz) = unpack(s);
            let si = g.get_idx(sx, sy, sz) as usize;
            sc.best[si] = 0.0;
            sc.gen_best[si] = gen;          // source: best=0, NO came (gen_came stays stale)
            let hh = ((sx - dx).abs() + (sy - dy).abs() + (sz - dz).abs()) as f64;
            sc.heap.push(HeapItem { f: hh, cost: 0.0, ord: ord_key(sx, sy, sz),
                                    node: s, nidx: si as i32 });
        }
        while let Some(it) = sc.heap.pop() {
            if it.node == dest {
                let mut path = vec![dest];
                let mut ci = dest_idx;
                while sc.gen_came[ci] == gen {
                    let p = sc.came[ci];
                    path.push(p);
                    let (px, py2, pz) = unpack(p);
                    ci = g.get_idx(px, py2, pz) as usize;
                }
                path.reverse();
                return Some(path);
            }
            let ui = it.nidx as usize;
            if sc.gen_best[ui] == gen && it.cost > sc.best[ui] + 1e-12 {
                continue;                   // stale heap entry
            }
            *expanded += 1;
            let (xi, yi, zi) = unpack(it.node);
            let nidx = it.nidx;
            let node = it.node;
            let cost = it.cost;
            // planar
            let on_term = self.sig.as_ref().map_or(false, |s| !s.contains(&zi));
            let win_here = win.contains(&xy_key(xi, yi)) && !self.no_jog.contains(&zi);
            let planar_ok = self.sig.is_none()
                || self.sig.as_ref().unwrap().contains(&zi)
                || win_here;
            if planar_ok {
                let is_h = g.z_is_h[zi as usize];
                let es = if is_h { 1 } else { g.ny };
                let ns = if is_h { g.nx } else { 1 };
                let pcost = if on_term { self.jog_cost } else { 1.0 };
                let term_ok = |nxk: i32, nyk: i32| -> bool {
                    !on_term || win.contains(&xy_key(nxk, nyk))
                };
                if xi + 1 < g.nx && in_corr(xi + 1, yi) && term_ok(xi + 1, yi)
                    && g.edge_e[nidx as usize] != 0 && g.blk_e[nidx as usize] == 0
                {
                    let ti = nidx + es;
                    if self.wire_ok_idx(ti, xi + 1, yi, zi, net) {
                        self.relax(sc, node, cost, pack(xi + 1, yi, zi), ti, xi + 1, yi, zi, pcost, dx, dy, dz, net, hard_avoid, self.track_cost(xi + 1, yi, zi));
                    }
                }
                if xi - 1 >= 0 && in_corr(xi - 1, yi) && term_ok(xi - 1, yi) {
                    let ei = nidx - es;
                    if g.edge_e[ei as usize] != 0 && g.blk_e[ei as usize] == 0
                        && self.wire_ok_idx(ei, xi - 1, yi, zi, net)
                    {
                        self.relax(sc, node, cost, pack(xi - 1, yi, zi), ei, xi - 1, yi, zi, pcost, dx, dy, dz, net, hard_avoid, self.track_cost(xi - 1, yi, zi));
                    }
                }
                if yi + 1 < g.ny && in_corr(xi, yi + 1) && term_ok(xi, yi + 1)
                    && g.edge_n[nidx as usize] != 0 && g.blk_n[nidx as usize] == 0
                {
                    let ti = nidx + ns;
                    if self.wire_ok_idx(ti, xi, yi + 1, zi, net) {
                        self.relax(sc, node, cost, pack(xi, yi + 1, zi), ti, xi, yi + 1, zi, pcost, dx, dy, dz, net, hard_avoid, self.track_cost(xi, yi + 1, zi));
                    }
                }
                if yi - 1 >= 0 && in_corr(xi, yi - 1) && term_ok(xi, yi - 1) {
                    let ei = nidx - ns;
                    if g.edge_n[ei as usize] != 0 && g.blk_n[ei as usize] == 0
                        && self.wire_ok_idx(ei, xi, yi - 1, zi, net)
                    {
                        self.relax(sc, node, cost, pack(xi, yi - 1, zi), ei, xi, yi - 1, zi, pcost, dx, dy, dz, net, hard_avoid, self.track_cost(xi, yi - 1, zi));
                    }
                }
            }
            // via U/D
            let on_portal = portals.contains(&xy_key(xi, yi));
            let in_window = win.contains(&xy_key(xi, yi));
            for nzi in [zi + 1, zi - 1] {
                if nzi < 0 || nzi >= g.nz {
                    continue;
                }
                let lo = zi.min(nzi);
                if !g.via_lo.contains(&lo) {
                    continue;
                }
                if let Some(s) = self.sig.as_ref() {
                    let touches_term = !s.contains(&zi) || !s.contains(&nzi);
                    if touches_term && !(on_portal || in_window) {
                        continue;
                    }
                }
                let vidx = g.get_idx(xi, yi, nzi);
                if (on_portal || g.fsc_via[vidx as usize] < BLOCK)
                    && self.wire_ok_idx(vidx, xi, yi, nzi, net)
                {
                    self.relax(sc, node, cost, pack(xi, yi, nzi), vidx, xi, yi, nzi, self.via_cost, dx, dy, dz, net, hard_avoid, 0.0);
                }
            }
        }
        None
    }

    #[inline]
    fn add_marker(&mut self, idx: i32) {
        let m = &mut self.marker[idx as usize]; // saturating steer cost (Python add_marker_planar)
        *m = (*m + 10).min(2000);
    }

    fn commit_node(&mut self, idx: i32, net: i32) {
        let cur = self.occ[idx as usize];
        if cur == -1 {
            self.occ[idx as usize] = net;
        } else if cur == net {
            // already ours
        } else if cur == -2 {
            let v = self.multi.entry(idx).or_default();
            if !v.contains(&net) {
                v.push(net);
            }
        } else {
            self.occ[idx as usize] = -2;
            self.multi.insert(idx, vec![cur, net]);
        }
    }

    fn rip_node(&mut self, idx: i32, net: i32) {
        let cur = self.occ[idx as usize];
        if cur == net {
            self.occ[idx as usize] = -1;
        } else if cur == -2 {
            if let Some(v) = self.multi.get_mut(&idx) {
                v.retain(|&n| n != net);
                if v.len() == 1 {
                    self.occ[idx as usize] = v[0];
                    self.multi.remove(&idx);
                } else if v.is_empty() {
                    self.occ[idx as usize] = -1;
                    self.multi.remove(&idx);
                }
            }
        }
    }

    fn commit(&mut self, net: i32, nodes: &[i64]) {
        for &p in nodes {
            let (xi, yi, zi) = unpack(p);
            self.commit_node(self.g.get_idx(xi, yi, zi), net);
        }
    }

    fn rip(&mut self, net: i32, nodes: &[i64]) {
        for &p in nodes {
            let (xi, yi, zi) = unpack(p);
            self.rip_node(self.g.get_idx(xi, yi, zi), net);
        }
    }

    // T4: bump per-track usage for this net's distinct signal tracks (delta +1 or -1)
    fn bump_tracks(&mut self, nodes: &[i64], delta: i32) {
        if self.track_weight == 0.0 {
            return;
        }
        let mut seen: HashSet<i64> = HashSet::new();
        for &p in nodes {
            let (x, y, z) = unpack(p);
            if self.sig.as_ref().map_or(false, |s| s.contains(&z)) {
                let perp = if self.g.z_is_h[z as usize] { y } else { x };
                let key = ((z as i64) << 32) | (perp as i64);
                if seen.insert(key) {
                    *self.track_use.entry(key).or_insert(0) += delta;
                }
            }
        }
    }

    // multi-terminal route: connect terminals to the seed tree (mirrors Python route_net).
    #[allow(clippy::too_many_arguments)]
    fn route_net(&self, sc: &mut Scratch, net: i32, terminals: &[i64], seed: &[i64],
                 portals: &HashSet<i64>, window: &HashSet<i64>,
                 corridor: Option<&HashSet<i64>>, gc: i32,
                 bx: Option<(i32, i32, i32, i32)>, hard_avoid: bool,
                 expanded: &mut u64) -> Option<(Vec<i64>, Vec<(i64, i64)>)> {
        let mut tree: HashSet<i64> = seed.iter().copied().collect();
        let mut node_set: HashSet<i64> = tree.clone();
        let mut edges: Vec<(i64, i64)> = Vec::new();
        let mut remaining: Vec<i64> = terminals.to_vec();
        if tree.is_empty() {
            if remaining.is_empty() {
                return Some((Vec::new(), Vec::new()));
            }
            let first = remaining.remove(0);
            tree.insert(first);
            node_set.insert(first);
        }
        for &t in &remaining {
            if tree.contains(&t) {
                continue;
            }
            match self.astar(sc, net, &tree, t, window, portals, corridor, gc, bx, hard_avoid, expanded) {
                Some(path) => {
                    for w in path.windows(2) {
                        edges.push((w[0], w[1]));
                    }
                    for &p in &path {
                        tree.insert(p);
                        node_set.insert(p);
                    }
                    tree.insert(t);
                    node_set.insert(t);
                }
                None => return None,
            }
        }
        let mut nodes: Vec<i64> = node_set.into_iter().collect();
        nodes.sort_unstable();
        Some((nodes, edges))
    }

    fn add_route(&mut self, net: i32, nodes: Vec<i64>, edges: Vec<(i64, i64)>) {
        self.commit(net, &nodes);
        self.routes.insert(net, nodes);
        self.edges.insert(net, edges);
    }

    fn remove_route(&mut self, net: i32) {
        if let Some(nodes) = self.routes.remove(&net) {
            self.rip(net, &nodes);
        }
        self.edges.remove(&net);
    }

    // every pin in one connected component of the net's edges (no-open guarantee)
    fn connected(&self, net: i32, terminals: &[i64]) -> bool {
        if terminals.len() <= 1 {
            return true;
        }
        let mut adj: HashMap<i64, Vec<i64>> = HashMap::new();
        for &(a, b) in self.edges.get(&net).map(|v| v.as_slice()).unwrap_or(&[]) {
            adj.entry(a).or_default().push(b);
            adj.entry(b).or_default().push(a);
        }
        let mut seen: HashSet<i64> = HashSet::new();
        seen.insert(terminals[0]);
        let mut stk = vec![terminals[0]];
        while let Some(n) = stk.pop() {
            if let Some(nbrs) = adj.get(&n) {
                for &m in nbrs {
                    if seen.insert(m) {
                        stk.push(m);
                    }
                }
            }
        }
        terminals.iter().all(|p| seen.contains(p))
    }

    // FlexDR worker-box: rip ONLY the net's edges fully inside ext, keep everything else,
    // reconnect the boundary anchors (inside endpoints of crossing edges) + in-box pins
    // within ext. Accept-or-revert on connectivity -> a box can never open the net.
    #[allow(clippy::too_many_arguments)]
    fn route_box(&mut self, sc: &mut Scratch, net: i32, ext: (i32, i32, i32, i32),
                 terminals: &[i64], portals: &HashSet<i64>, gcell: i32,
                 hard_avoid: bool, expanded: &mut u64) -> bool {
        let inb = |p: i64| -> bool {
            let (x, y, _z) = unpack(p);
            x >= ext.0 && y >= ext.1 && x <= ext.2 && y <= ext.3
        };
        let edges = match self.edges.get(&net) {
            Some(e) => e.clone(),
            None => return false,
        };
        let has_rip = edges.iter().any(|&(a, b)| inb(a) && inb(b));
        if !has_rip {
            return false;
        }
        let keep: Vec<(i64, i64)> = edges.iter().copied()
            .filter(|&(a, b)| !(inb(a) && inb(b))).collect();
        let mut anchors: HashSet<i64> = HashSet::new();
        for &(a, b) in &keep {
            if inb(a) != inb(b) {
                anchors.insert(if inb(a) { a } else { b });
            }
        }
        for &p in terminals {
            if inb(p) {
                anchors.insert(p);
            }
        }
        let keep_nodes: HashSet<i64> = keep.iter().flat_map(|&(a, b)| [a, b]).collect();

        let saved_r = self.routes.get(&net).cloned().unwrap_or_default();
        let saved_e = edges;
        self.remove_route(net);

        let (new_nodes, new_edges) = if anchors.len() <= 1 {
            let mut n: Vec<i64> = keep_nodes.into_iter().collect();
            n.sort_unstable();
            (n, keep)
        } else {
            let mut av: Vec<i64> = anchors.into_iter().collect();
            av.sort_unstable();
            // reconnect within ext, no jogs (empty window) -> never a terminal-layer short
            match self.route_net(sc, net, &av, &[], portals, &HashSet::new(),
                                 None, gcell, Some(ext), hard_avoid, expanded) {
                None => {
                    self.add_route(net, saved_r, saved_e);   // revert -> no open
                    return false;
                }
                Some((rnodes, redges)) => {
                    let mut nset = keep_nodes;
                    nset.extend(rnodes);
                    let mut n: Vec<i64> = nset.into_iter().collect();
                    n.sort_unstable();
                    let mut e = keep;
                    e.extend(redges);
                    (n, e)
                }
            }
        };
        self.add_route(net, new_nodes, new_edges);
        if !self.connected(net, terminals) {
            self.remove_route(net);
            self.add_route(net, saved_r, saved_e);           // REVERT -> no open, ever
            return false;
        }
        true
    }
}

/// route_all: per-net initial routing (sorted by net_id). Maintains flat occupancy. Each
/// net connects its terminals to its seed tree. Returns per-net (net_id, packed nodes,
/// packed edges) + total A* expansions. Python keeps consume_seed/overlap-loop/route_box.
#[pyfunction]
fn route_all(
    py: Python<'_>,
    nx: i32,
    ny: i32,
    nz: i32,
    z_is_h: Vec<bool>,
    via_lo: Vec<i32>,
    edge_e: Vec<u8>,
    edge_n: Vec<u8>,
    blk_e: Vec<u8>,
    blk_n: Vec<u8>,
    fsc_h: Vec<u8>,
    fsc_v: Vec<u8>,
    fsc_via: Vec<u8>,
    mc: Vec<u8>,
    pad_owner: Vec<(i32, i32)>,
    marker_weight: f64,
    occ_penalty: f64,
    via_cost: f64,
    jog_cost: f64,
    spacing_halo: i32,
    sig_layers: Vec<i32>,
    no_jog_layers: Vec<i32>,
    // per net (already sorted by net_id): (net_id, terminals, portals_xy, window_xy, seed)
    nets: Vec<(i32, Vec<i64>, Vec<i64>, Vec<i64>, Vec<i64>)>,
    seed_occ: Vec<(i32, i32)>, // (flat_idx, net_id) pre-occupied (the seed)
    ovlp_passes: i32,          // 0 = initial routing only (byte-parity); >0 = resolve overlaps
    corridors: Vec<(i32, Vec<i64>)>, // T3 guide: (net_id, packed gcells gx|gy<<32); empty = none
    gcell: i32,                // gcell size (0/empty corridors => unbounded)
    track_weight: f64,         // T4 track-balancing weight (0 = off)
) -> PyResult<(Vec<(i32, Vec<i64>, Vec<(i64, i64)>)>, u64, u64, i64, u64)> {
    let g = Grid {
        nx, ny, nz, xy: nx * ny, z_is_h,
        via_lo: via_lo.into_iter().collect(),
        edge_e: &edge_e, edge_n: &edge_n, blk_e: &blk_e, blk_n: &blk_n,
        fsc_h: &fsc_h, fsc_v: &fsc_v, fsc_via: &fsc_via, mc: &mc,
    };
    let pad_map: HashMap<i32, i32> = pad_owner.into_iter().collect();
    let cap = (nx * ny * nz) as usize;
    let sig = if sig_layers.is_empty() {
        None
    } else {
        Some(sig_layers.into_iter().collect::<HashSet<i32>>())
    };
    let mut maze = Maze {
        g, occ: vec![-1i32; cap], pad_owner: &pad_map, marker_weight, occ_penalty, via_cost,
        jog_cost, spacing_halo, sig, no_jog: no_jog_layers.into_iter().collect(),
        marker: vec![0i32; cap], multi: HashMap::new(),
        routes: HashMap::new(), edges: HashMap::new(),
        track_use: HashMap::new(), track_weight,
    };
    for (idx, nid) in &seed_occ {
        maze.commit_node(*idx, *nid);
    }
    let mut sc = Scratch::new(cap);
    let mut expanded: u64 = 0;

    // per-net data
    let mut order: Vec<i32> = Vec::with_capacity(nets.len());
    let mut terms_of: HashMap<i32, Vec<i64>> = HashMap::new();
    let mut portals_of: HashMap<i32, HashSet<i64>> = HashMap::new();
    let mut window_of: HashMap<i32, HashSet<i64>> = HashMap::new();
    let mut seed_of: HashMap<i32, Vec<i64>> = HashMap::new();
    for (net_id, terms, portals_v, window_v, seed_v) in nets {
        order.push(net_id);
        terms_of.insert(net_id, terms);
        portals_of.insert(net_id, portals_v.into_iter().collect());
        window_of.insert(net_id, window_v.into_iter().collect());
        seed_of.insert(net_id, seed_v);
    }
    let corr_of: HashMap<i32, HashSet<i64>> =
        corridors.into_iter().map(|(n, gc)| (n, gc.into_iter().collect())).collect();

    // route a net inside its T3 corridor; if no in-corridor path, retry UNBOUNDED (scope #4).
    macro_rules! route_bounded {
        ($maze:expr, $sc:expr, $net:expr, $seed:expr, $exp:expr) => {{
            let corr = corr_of.get(&$net);
            let mut r = $maze.route_net(&mut $sc, $net, &terms_of[&$net], $seed,
                                        &portals_of[&$net], &window_of[&$net], corr, gcell, None, false, &mut $exp);
            if r.is_none() && corr.is_some() {
                r = $maze.route_net(&mut $sc, $net, &terms_of[&$net], $seed,
                                    &portals_of[&$net], &window_of[&$net], None, gcell, None, false, &mut $exp);
            }
            r
        }};
    }
    let (gnx, gny) = (nx, ny);

    // initial per-net routing (connect terminals to seed tree), commit occupancy
    for &net in &order {
        py.check_signals()?;
        let seed = seed_of[&net].clone();
        let r = route_bounded!(maze, sc, net, &seed, expanded);
        match r {
            Some((nodes, edges)) => {
                maze.bump_tracks(&nodes, 1);     // T4: this net's tracks now busier
                maze.add_route(net, nodes, edges);
            }
            None => maze.add_route(net, Vec::new(), Vec::new()),
        }
    }

    let exp_init = expanded;                     // diagnostic: split initial vs overlap cost
    let born_overlaps = maze.multi.len() as u64; // overlaps BORN by initial routing (scope B)
    let mut passes_run = 0i64;
    // OVERLAP RESOLUTION via FlexDR WORKER-BOX (segment-level): for each overlapped net, rip
    // only its in-ext route segment (a small box around the overlap) and reconnect the
    // boundary anchors locally with hard_avoid (never a new overlap) -> the overlap set
    // strictly shrinks AND each reroute is local/cheap. Whole-net reroute is the fallback.
    for _ in 0..ovlp_passes {
        if maze.multi.is_empty() {
            break;
        }
        passes_run += 1;
        let over: Vec<i32> = maze.multi.keys().copied().collect();
        let mut fixnets: HashSet<i32> = HashSet::new();
        for &c in &over {
            maze.add_marker(c);
            if let Some(&hi) = maze.multi.get(&c).and_then(|v| v.iter().max()) {
                fixnets.insert(hi);
            }
        }
        let mut fixv: Vec<i32> = fixnets.into_iter().collect();
        fixv.sort_unstable();
        for net in fixv {
            py.check_signals()?;
            let old = maze.routes.get(&net).cloned().unwrap_or_default();
            maze.remove_route(net);
            // box-local SOFT reroute (the measured-best baseline, 63s/cpu16): bound to the
            // net's bbox so it stays local, marker cost steers it off contested cells. (The
            // worker-box route_box infra is kept but NOT used for the bulk -- every hard/soft
            // worker-box variant measured WORSE: convergence pass count is the real wall.)
            let mut x0 = i32::MAX; let mut y0 = i32::MAX; let mut x1 = 0; let mut y1 = 0;
            for &p in terms_of[&net].iter().chain(old.iter()) {
                let (xi, yi, _z) = unpack(p);
                x0 = x0.min(xi); y0 = y0.min(yi); x1 = x1.max(xi); y1 = y1.max(yi);
            }
            let mut r = None;
            if x0 <= x1 {
                for m in [24, 96, 300] {
                    let bx = Some(((x0 - m).max(0), (y0 - m).max(0),
                                   (x1 + m).min(gnx - 1), (y1 + m).min(gny - 1)));
                    r = maze.route_net(&mut sc, net, &terms_of[&net], &[],
                                       &portals_of[&net], &window_of[&net], None, gcell, bx, false, &mut expanded);
                    if r.is_some() {
                        break;
                    }
                }
            }
            if r.is_none() {
                r = maze.route_net(&mut sc, net, &terms_of[&net], &[],
                                   &portals_of[&net], &window_of[&net], None, gcell, None, false, &mut expanded);
            }
            match r {
                Some((nodes, edges)) => maze.add_route(net, nodes, edges),
                None => maze.add_route(net, old, Vec::new()),
            }
        }
    }

    let mut out: Vec<(i32, Vec<i64>, Vec<(i64, i64)>)> = Vec::with_capacity(order.len());
    for &net in &order {
        out.push((net, maze.routes.remove(&net).unwrap_or_default(),
                  maze.edges.remove(&net).unwrap_or_default()));
    }
    Ok((out, expanded, exp_init, passes_run, born_overlaps))
}

#[pyfunction]
fn ping() -> i64 {
    42
}

#[pymodule]
fn klink_trackmaze_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    m.add_function(wrap_pyfunction!(route_all, m)?)?;
    Ok(())
}
