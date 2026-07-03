// klink_boxmaze_rs -- Rust port of the faithful FlexDR box_maze A* kernel.
//
// BYTE-PARITY CONTRACT: this must return the EXACT same cell path as the Python
// `box_maze` in klink/routing/backends/flexdr/flexdr.py for identical inputs.
// The path is determined by the A* pop order, which Python fixes via the heap
// key (f, g_cost, state) where state = ((ix,iy,layer), dir). We replicate that
// key exactly: (f, cost, ix, iy, layer, dir_rank) ascending, with dir_rank in
// the SAME order as Python compares the direction strings: D<E<N<S<U<W.
//
// Data layout follows OpenROAD FlexGridGraph (research_refs/.../drt/src/dr/
// FlexGridGraph.h): a flat per-node array indexed by a packed (x,y,z) int, with
// the constant grid legality (blocked / pad-owner) as flat arrays built ONCE,
// and the per-box cost classes (hard / routeShape / marker / fixedShape) passed
// per call as small box-local sets. No process/DRC constants live here -- every
// cost/keep-out is supplied by the caller as data (process- & device-general).
#![allow(deprecated)]

use pyo3::prelude::*;
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashMap, HashSet, VecDeque};

// dir ranks == Python string order of _dir() results: D<E<N<S<U<W ; None = 6.
const D_D: u8 = 0; // down  (layer-)
const D_E: u8 = 1; // +x
const D_N: u8 = 2; // +y
const D_S: u8 = 3; // -y
const D_U: u8 = 4; // up    (layer+)
const D_W: u8 = 5; // -x
const D_NONE: u8 = 6;

#[inline(always)]
fn dir_rank(al: i32, bl: i32, dx: i32, dy: i32) -> u8 {
    if al != bl {
        if bl > al {
            D_U
        } else {
            D_D
        }
    } else if dx == 1 && dy == 0 {
        D_E
    } else if dx == -1 && dy == 0 {
        D_W
    } else if dx == 0 && dy == 1 {
        D_N
    } else {
        D_S
    }
}

/// Via cells = both endpoints of every edge that is a pure layer change
/// (same x,y, different layer). Matches Python `_via_cells` as a SET (order
/// irrelevant: every consumer builds a membership set from it).
fn via_from_edges(edges: &[((i32, i32, i32), (i32, i32, i32))]) -> Vec<(i32, i32, i32)> {
    let mut out: Vec<(i32, i32, i32)> = Vec::new();
    let mut seen: HashSet<(i32, i32, i32)> = HashSet::new();
    for (a, b) in edges {
        if a.0 == b.0 && a.1 == b.1 && a.2 != b.2 {
            if seen.insert(*a) {
                out.push(*a);
            }
            if seen.insert(*b) {
                out.push(*b);
            }
        }
    }
    out
}

/// Faithful to Python `cell_in_box`: the cell's gcell lies in [gx0,gx1] x
/// [gy0,gy1]. Cell coords are >= 0 so integer `/` == Python floor `//`.
#[inline(always)]
fn cell_in_box(c: (i32, i32, i32), gx0: i32, gy0: i32, gx1: i32, gy1: i32, gc: i32) -> bool {
    let (gx, gy) = (c.0 / gc, c.1 / gc);
    gx0 <= gx && gx <= gx1 && gy0 <= gy && gy <= gy1
}

// Min-heap item: BinaryHeap is a max-heap, so Ord is REVERSED -> the natural
// smallest (f, cost, ix, iy, layer, dir) is popped first (== Python heapq).
struct HeapItem {
    f: f64,
    cost: f64,
    ix: i32,
    iy: i32,
    layer: i32,
    dir: u8,
    state: i64,
}

impl PartialEq for HeapItem {
    fn eq(&self, o: &Self) -> bool {
        self.f == o.f
            && self.cost == o.cost
            && self.ix == o.ix
            && self.iy == o.iy
            && self.layer == o.layer
            && self.dir == o.dir
    }
}
impl Eq for HeapItem {}
impl PartialOrd for HeapItem {
    fn partial_cmp(&self, o: &Self) -> Option<Ordering> {
        Some(self.cmp(o))
    }
}
impl Ord for HeapItem {
    fn cmp(&self, o: &Self) -> Ordering {
        // reversed: self is "greater" (popped earlier) when its key is smaller
        o.f.partial_cmp(&self.f)
            .unwrap()
            .then_with(|| o.cost.partial_cmp(&self.cost).unwrap())
            .then_with(|| o.ix.cmp(&self.ix))
            .then_with(|| o.iy.cmp(&self.iy))
            .then_with(|| o.layer.cmp(&self.layer))
            .then_with(|| o.dir.cmp(&self.dir))
    }
}

#[pyclass]
struct Grid {
    nx: i32,
    ny: i32,
    nz: i32,
    plane: i64, // nx*ny
    blocked: Vec<bool>,    // flat [idx] : channel keep-out (all nets)
    pad_owner: Vec<i32>,   // flat [idx] : owner net id, -1 = none
    via_blocked: HashSet<i64>, // (iy*nx+ix)
    via_index: Vec<(i32, i32, f64)>, // (a_layer, b_layer, cost) -- caller-sorted
    // --- Stage 3b: persistent incremental occupancy (signal nets only) ---------
    // occ[cell_idx] = {net_id: refcount} of every SIGNAL net whose halo-expanded
    // footprint covers the cell. Maintained incrementally by route_box so box_maze
    // can read routeShape membership ("a foreign signal net is here") in O(1)
    // instead of Python rebuilding the union of all foreign footprints per
    // reroute. Byte-parity-safe: a SET union, order- and tie-break-independent.
    occ: HashMap<i64, HashMap<i32, u32>>,
    occ_wire_halo: i32,
    occ_via_halo: i32,
    // --- Stage 3c: persistent routes store (signal + supply) -------------------
    // The global routes kept IN RUST, in routes-dict order, so flexgc reads them
    // in place (no per-box full-routes marshaling) and its owner/cover first-touch
    // order == Python's routes-dict order (the one order-dependent parity hook).
    // Parallel arrays indexed by "slot" (= routes-dict insertion order); a net's
    // slot is fixed once init'd (dict update preserves position), so an in-box
    // reroute or a committed global update mutates the slot in place.
    route_net: Vec<i32>,                  // net id per slot, in routes-dict order
    route_cells: Vec<Vec<(i32, i32, i32)>>, // route cells per slot
    route_via: Vec<Vec<(i32, i32, i32)>>,   // via cells per slot (derived from edges)
    route_edges: Vec<Vec<Edge>>,            // full edge list per slot (extract_box needs it)
    slot_of_net: HashMap<i32, usize>,       // net id -> slot
    // --- Stage 3c-1b: constant worker inputs (set once before the schedule) -----
    term_sets: HashMap<i32, Vec<Vec<(i32, i32, i32)>>>, // net id -> its terminal cell-sets
    supply_ids: HashSet<i32>,               // supply net ids (hard keep-outs, never ripped)
    obstacles: HashSet<i64>,                // global obstacle cell idxs (always hard)
    // net id -> rank in NAME-sorted order. The worker queue is canonical-sorted by
    // net NAME (Python `sorted(names)`, 3b-1); net ids are NOT name-ordered, so all
    // enqueue sorts use this rank to stay byte-parity.
    name_rank: HashMap<i32, i32>,
}

type Cell = (i32, i32, i32);
type Edge = (Cell, Cell);
/// A worker box's local route overrides: net id -> (cells, edges) for the nets
/// it has rerouted. flexgc/extract read these in place of the persistent store.
type Overlay = HashMap<i32, (Vec<Cell>, Vec<Edge>)>;

impl Grid {
    #[inline(always)]
    fn idx(&self, ix: i32, iy: i32, layer: i32) -> i64 {
        (layer as i64 * self.ny as i64 + iy as i64) * self.nx as i64 + ix as i64
    }
    #[inline(always)]
    fn in_bounds(&self, ix: i32, iy: i32) -> bool {
        ix >= 0 && ix < self.nx && iy >= 0 && iy < self.ny
    }
    #[inline(always)]
    fn wire_ok(&self, ix: i32, iy: i32, layer: i32, net: i32) -> bool {
        let id = self.idx(ix, iy, layer) as usize;
        if self.blocked[id] {
            return false;
        }
        let owner = self.pad_owner[id];
        owner < 0 || owner == net
    }
    /// True if some net OTHER than `net` has footprint occupancy at `cell` -- the
    /// occ-backed routeShape test (== Python `nxt in route_shape`, where
    /// route_shape excludes the net being routed).
    #[inline(always)]
    fn occ_has_foreign(&self, cell: i64, net: i32) -> bool {
        match self.occ.get(&cell) {
            None => false,
            Some(m) => m.keys().any(|&k| k != net),
        }
    }

    /// occ_has_foreign over the global occ + a per-worker LOCAL signed delta. The
    /// worker (route_box_plan) keeps its reroutes in `local` instead of mutating
    /// the shared global occ (so a batch of workers can read the global occ in
    /// parallel). EQUALS what occ_has_foreign would return after the worker's prior
    /// reroutes were applied to the global occ -> byte-parity with the serial
    /// transaction path.
    fn occ_has_foreign_ov(
        &self,
        local: &HashMap<i64, HashMap<i32, i32>>,
        cell: i64,
        net: i32,
    ) -> bool {
        let g = self.occ.get(&cell);
        match local.get(&cell) {
            None => match g {
                None => false,
                Some(m) => m.keys().any(|&k| k != net),
            },
            Some(lm) => {
                if let Some(gm) = g {
                    for (&k, &gc) in gm {
                        if k != net && gc as i32 + lm.get(&k).copied().unwrap_or(0) > 0 {
                            return true;
                        }
                    }
                    lm.iter()
                        .any(|(&k, &d)| k != net && !gm.contains_key(&k) && d > 0)
                } else {
                    lm.iter().any(|(&k, &d)| k != net && d > 0)
                }
            }
        }
    }

    /// Apply a net's halo footprint to a LOCAL signed delta (+1 add / -1 remove per
    /// footprint cell), same halo expansion as occ_apply_cells. Does not touch the
    /// global occ -> parallel-safe (each worker owns its `local`).
    fn occ_apply_local(
        &self,
        local: &mut HashMap<i64, HashMap<i32, i32>>,
        net: i32,
        cells: &[Cell],
        via: &[Cell],
        add: bool,
    ) {
        let mut via_set: HashSet<i64> = HashSet::with_capacity(via.len());
        for c in via {
            via_set.insert(self.idx(c.0, c.1, c.2));
        }
        let (wh, vh) = (self.occ_wire_halo, self.occ_via_halo);
        let step: i32 = if add { 1 } else { -1 };
        for c in cells {
            let halo = if via_set.contains(&self.idx(c.0, c.1, c.2)) {
                vh
            } else {
                wh
            };
            let mut dx = -halo;
            while dx <= halo {
                let mut dy = -halo;
                while dy <= halo {
                    let (nx, ny) = (c.0 + dx, c.1 + dy);
                    if self.in_bounds(nx, ny) {
                        *local
                            .entry(self.idx(nx, ny, c.2))
                            .or_default()
                            .entry(net)
                            .or_insert(0) += step;
                    }
                    dy += 1;
                }
                dx += 1;
            }
        }
    }

    /// Merge a worker's accepted LOCAL signed delta into the global occ (serial,
    /// in deterministic box order) -- the only writer of the global occ during a
    /// pass, so the parallel section stays read-only.
    fn occ_merge_local(&mut self, local: &HashMap<i64, HashMap<i32, i32>>) {
        for (&cell, lm) in local {
            let entry = self.occ.entry(cell).or_default();
            for (&net, &d) in lm {
                if d == 0 {
                    continue;
                }
                let c = entry.entry(net).or_insert(0);
                let nv = *c as i64 + d as i64;
                if nv <= 0 {
                    entry.remove(&net);
                } else {
                    *c = nv as u32;
                }
            }
            if entry.is_empty() {
                self.occ.remove(&cell);
            }
        }
    }
    /// Add/remove one net's halo-expanded footprint to/from `occ` (no logging).
    /// via cells use `occ_via_halo`, others `occ_wire_halo` -- identical halo
    /// semantics to Python `_net_footprint`/`_footprint`, so the occ membership
    /// set equals the Python route_shape union exactly.
    fn occ_apply_cells(
        &mut self,
        net: i32,
        cells: &[(i32, i32, i32)],
        via: &[(i32, i32, i32)],
        add: bool,
    ) {
        let mut via_set: HashSet<i64> = HashSet::with_capacity(via.len());
        for (ix, iy, l) in via {
            via_set.insert(self.idx(*ix, *iy, *l));
        }
        let (wh, vh) = (self.occ_wire_halo, self.occ_via_halo);
        for (ix, iy, l) in cells {
            let halo = if via_set.contains(&self.idx(*ix, *iy, *l)) {
                vh
            } else {
                wh
            };
            let mut dx = -halo;
            while dx <= halo {
                let mut dy = -halo;
                while dy <= halo {
                    let (nx, ny) = (ix + dx, iy + dy);
                    if self.in_bounds(nx, ny) {
                        let f = self.idx(nx, ny, *l);
                        if add {
                            *self.occ.entry(f).or_default().entry(net).or_insert(0) += 1;
                        } else {
                            let empty = if let Some(m) = self.occ.get_mut(&f) {
                                if let Some(c) = m.get_mut(&net) {
                                    *c -= 1;
                                    if *c == 0 {
                                        m.remove(&net);
                                    }
                                }
                                m.is_empty()
                            } else {
                                false
                            };
                            if empty {
                                self.occ.remove(&f);
                            }
                        }
                    }
                    dy += 1;
                }
                dx += 1;
            }
        }
    }
}

#[pymethods]
impl Grid {
    #[new]
    fn new(
        nx: i32,
        ny: i32,
        nz: i32,
        blocked: Vec<(i32, i32, i32)>,        // (layer, ix, iy)
        pad_owner: Vec<(i32, i32, i32, i32)>, // (layer, ix, iy, owner_id)
        via_blocked: Vec<(i32, i32)>,         // (ix, iy)
        via_index: Vec<(i32, i32, f64)>,      // (a_layer, b_layer, cost) sorted by caller
    ) -> Self {
        let n = (nx as usize) * (ny as usize) * (nz as usize);
        let mut g = Grid {
            nx,
            ny,
            nz,
            plane: nx as i64 * ny as i64,
            blocked: vec![false; n],
            pad_owner: vec![-1i32; n],
            via_blocked: HashSet::with_capacity(via_blocked.len()),
            via_index,
            occ: HashMap::new(),
            occ_wire_halo: 0,
            occ_via_halo: 0,
            route_net: Vec::new(),
            route_cells: Vec::new(),
            route_via: Vec::new(),
            route_edges: Vec::new(),
            slot_of_net: HashMap::new(),
            term_sets: HashMap::new(),
            supply_ids: HashSet::new(),
            obstacles: HashSet::new(),
            name_rank: HashMap::new(),
        };
        for (layer, ix, iy) in blocked {
            if ix >= 0 && ix < nx && iy >= 0 && iy < ny && layer >= 0 && layer < nz {
                let id = g.idx(ix, iy, layer) as usize;
                g.blocked[id] = true;
            }
        }
        for (layer, ix, iy, owner) in pad_owner {
            if ix >= 0 && ix < nx && iy >= 0 && iy < ny && layer >= 0 && layer < nz {
                let id = g.idx(ix, iy, layer) as usize;
                g.pad_owner[id] = owner;
            }
        }
        for (ix, iy) in via_blocked {
            g.via_blocked.insert(iy as i64 * nx as i64 + ix as i64);
        }
        g
    }

    /// Faithful A* (spec section 3). Returns the cell path [(ix,iy,layer),...]
    /// or None. via_halo is intentionally absent: the Python box_maze ignores
    /// the halo that _neighbors yields (it only uses nxt + the via `extra`).
    #[allow(clippy::too_many_arguments)]
    fn box_maze(
        &self,
        net: i32,
        starts: Vec<(i32, i32, i32)>,
        goals: Vec<(i32, i32, i32)>,
        hard: Vec<(i32, i32, i32)>,
        route_shape: Vec<(i32, i32, i32)>,
        marker_pos: Vec<(i32, i32, i32)>, // cells with marker counter > 0
        fixed: Vec<(i32, i32, i32)>,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        corridor: Vec<(i32, i32)>, // gcells
        gc: i32,
        use_occ: bool, // Stage 3b: read routeShape from persistent occ (ignore route_shape arg)
    ) -> Option<Vec<(i32, i32, i32)>> {
        let to_set = |v: Vec<(i32, i32, i32)>| -> HashSet<i64> {
            let mut s = HashSet::with_capacity(v.len());
            for (ix, iy, l) in v {
                s.insert(self.idx(ix, iy, l));
            }
            s
        };
        let hard_set = to_set(hard);
        let route_set = to_set(route_shape);
        let marker_set = to_set(marker_pos);
        let fixed_set = to_set(fixed);
        let mut corridor_gkeys: HashSet<i64> = HashSet::with_capacity(corridor.len());
        for (gx, gy) in corridor {
            corridor_gkeys.insert(gx as i64 * 2_000_000i64 + gy as i64);
        }
        self.maze_run(
            net, &starts, &goals, &hard_set, &route_set, &marker_set, &fixed_set, gg_drc,
            gg_marker, gg_fixed, &corridor_gkeys, gc, use_occ, None,
        )
    }

    // --- Persistent occupancy (signal-only, halo-expanded) --------------------
    /// (Re)initialize occ from scratch: clear it, set the halos, add every net's
    /// footprint. Called once after initial routing (occ then tracks the global
    /// signal routes; the parallel worker reads it + a local signed-delta and
    /// merges accepted deltas via occ_merge_local). nets = [(net_id, cells, via)].
    fn occ_init(
        &mut self,
        nets: Vec<(i32, Vec<(i32, i32, i32)>, Vec<(i32, i32, i32)>)>,
        wire_halo: i32,
        via_halo: i32,
    ) {
        self.occ.clear();
        self.occ_wire_halo = wire_halo;
        self.occ_via_halo = via_halo;
        for (nid, cells, via) in &nets {
            self.occ_apply_cells(*nid, cells, via, true);
        }
    }

    /// Order-independent signature of the current occ (sum of per-(cell,net,count)
    /// contributions, wrapping). Two occ states with the same (cell,net)->count
    /// map give the same value -> a cheap drift guard: an incrementally maintained
    /// occ must equal a fresh occ_init from the same routes.
    fn occ_signature(&self) -> u64 {
        let mut acc: u64 = 0;
        for (&cell, m) in &self.occ {
            for (&net, &cnt) in m {
                let c = (cell as u64).wrapping_mul(1_000_003)
                    ^ (net as u64).wrapping_mul(2_654_435_761)
                    ^ (cnt as u64).wrapping_mul(40_503);
                acc = acc.wrapping_add(c.wrapping_mul(0x9E37_79B9_7F4A_7C15));
            }
        }
        acc
    }

    /// Faithful FlexGC-lite (spec section 6): cover-based short/spacing markers
    /// (a cell where >=2 nets' halo footprints overlap) + parallel-run-length
    /// spacing markers. Marshaled form: `nets` = (net_id, route_cells, via_cells)
    /// in routes-dict order. Delegates to `flexgc_core` (see there for the
    /// byte-parity / order contract).
    #[allow(clippy::too_many_arguments)]
    fn flexgc(
        &self,
        nets: Vec<(i32, Vec<(i32, i32, i32)>, Vec<(i32, i32, i32)>)>,
        wire_halo: i32,
        via_halo: i32,
        region: Option<(i32, i32, i32, i32)>,
        prl_halo: i32,
        prl_len: i32,
        vertical: Vec<bool>,
    ) -> Vec<(Vec<(i32, i32, i32)>, i32, Vec<i32>)> {
        self.flexgc_core(
            nets.iter().map(|(n, c, v)| (*n, c.as_slice(), v.as_slice())),
            wire_halo,
            via_halo,
            region,
            prl_halo,
            prl_len,
            &vertical,
        )
    }

    // --- Stage 3c: persistent routes store + in-place flexgc -------------------
    /// (Re)load the global routes into the Rust store, in routes-dict order
    /// (signal AND supply -- flexgc covers every net). `nets` = (net_id,
    /// route_cells, via_cells). A net's slot is fixed here; later `routes_update`
    /// mutates it in place so the flexgc owner/cover first-touch order stays ==
    /// Python's routes-dict order (the one order-dependent parity hook).
    fn routes_init(&mut self, nets: Vec<(i32, Vec<Cell>, Vec<Edge>)>) {
        self.route_net.clear();
        self.route_cells.clear();
        self.route_via.clear();
        self.route_edges.clear();
        self.slot_of_net.clear();
        for (nid, cells, edges) in nets {
            let via = via_from_edges(&edges);
            self.slot_of_net.insert(nid, self.route_net.len());
            self.route_net.push(nid);
            self.route_cells.push(cells);
            self.route_via.push(via);
            self.route_edges.push(edges);
        }
    }

    /// Replace one net's stored route+edges in place (the net keeps its slot, so
    /// order is preserved). Called after a worker box commits a global change.
    fn routes_update(&mut self, net: i32, cells: Vec<Cell>, edges: Vec<Edge>) {
        if let Some(&s) = self.slot_of_net.get(&net) {
            self.route_via[s] = via_from_edges(&edges);
            self.route_cells[s] = cells;
            self.route_edges[s] = edges;
        }
    }

    /// Set the constant per-net terminal cell-sets (net id -> list of terminal
    /// cell sets), used by the worker's extract_box reroute targets. Set once.
    fn set_termsets(&mut self, termsets: Vec<(i32, Vec<Vec<Cell>>)>) {
        self.term_sets.clear();
        for (nid, sets) in termsets {
            self.term_sets.insert(nid, sets);
        }
    }

    /// Set the supply net ids (hard keep-outs, never ripped) + global obstacle
    /// cells. Set once before the schedule.
    fn set_worker_consts(&mut self, supply: Vec<i32>, obstacles: Vec<Cell>) {
        self.supply_ids = supply.into_iter().collect();
        self.obstacles = obstacles
            .into_iter()
            .map(|(ix, iy, l)| self.idx(ix, iy, l))
            .collect();
    }

    /// Set net id -> NAME-sorted rank (the worker queue order key). Set once.
    fn set_name_rank(&mut self, ranks: Vec<(i32, i32)>) {
        self.name_rank = ranks.into_iter().collect();
    }

    /// Stage 3c-2: run a CHECKERBOARD batch of boxes in PARALLEL (the boxes are
    /// region-disjoint incl DRC halo, so they read the shared grid + global occ
    /// read-only without interfering), then SERIAL-merge each box's occ delta +
    /// IN-BOX route portion in the given (deterministic) box order -> parallel ==
    /// serial == byte-parity. `n_threads` is auto-detected by the caller
    /// (os.cpu_count(), config override); clamped to [1, batch size]. Returns the
    /// COMPOSED per-net result; the caller applies it to the store. `boxes` =
    /// [(gx0,gy0,gx1,gy1), ...].
    #[allow(clippy::too_many_arguments)]
    fn route_boxes(
        &mut self,
        py: Python<'_>,
        boxes: Vec<(i32, i32, i32, i32)>,
        gc: i32,
        wire_halo: i32,
        via_halo: i32,
        ripup_all: bool,
        maze_end_iter: i32,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        marker_decay: f64,
        prl_halo: i32,
        prl_len: i32,
        vertical: Vec<bool>,
        n_threads: i32,
    ) -> Vec<(i32, Vec<Cell>, Vec<Edge>)> {
        type Plan = (Vec<(i32, Vec<Cell>, Vec<Edge>)>, HashMap<i64, HashMap<i32, i32>>, bool);
        let n = boxes.len();
        if n == 0 {
            return Vec::new();
        }
        let nthreads = (n_threads.max(1) as usize).min(n);
        let this: &Grid = &*self;
        let vert: &[bool] = &vertical;
        let boxes_ref: &[(i32, i32, i32, i32)] = &boxes;

        // parallel section: compute each box's plan with the GIL released. Each
        // plan is &self + owned locals -> no shared mutation, no data race.
        let plans: Vec<Plan> = py.allow_threads(|| {
            let next = std::sync::atomic::AtomicUsize::new(0);
            let mut slots: Vec<Option<Plan>> = (0..n).map(|_| None).collect();
            std::thread::scope(|s| {
                let handles: Vec<_> = (0..nthreads)
                    .map(|_| {
                        s.spawn(|| {
                            let mut local: Vec<(usize, Plan)> = Vec::new();
                            loop {
                                let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                                if i >= n {
                                    break;
                                }
                                let (gx0, gy0, gx1, gy1) = boxes_ref[i];
                                let plan = this.route_box_plan(
                                    gx0, gy0, gx1, gy1, gc, wire_halo, via_halo, ripup_all,
                                    maze_end_iter, gg_drc, gg_marker, gg_fixed, marker_decay,
                                    prl_halo, prl_len, vert,
                                );
                                local.push((i, plan));
                            }
                            local
                        })
                    })
                    .collect();
                for h in handles {
                    for (i, plan) in h.join().unwrap() {
                        slots[i] = Some(plan);
                    }
                }
            });
            slots.into_iter().map(|o| o.unwrap()).collect()
        });

        // Serial deterministic merge in box order. Two DISJOINT same-batch boxes
        // can both reroute one (long) net; serial composes them via read-after-write
        // (box B rebuilds the net from box A's committed route). We replicate that by
        // composing each box's IN-BOX portion into the net (the boxes are region-
        // disjoint, so per-box composition == serial). occ deltas are already region-
        // local (a box's remove(old)+add(new) cancels outside its region), so merging
        // them is correct. Returns the COMPOSED routes for all nets the batch changed.
        let mut merged: HashMap<i32, (Vec<Cell>, Vec<Edge>)> = HashMap::new();
        for ((delta, local_occ, accept), &(gx0, gy0, gx1, gy1)) in
            plans.into_iter().zip(boxes_ref.iter())
        {
            if !accept {
                continue;
            }
            self.occ_merge_local(&local_occ);
            for (net, nw_cells, nw_edges) in delta {
                let (cur_cells, cur_edges) = merged.remove(&net).unwrap_or_else(|| {
                    match self.slot_of_net.get(&net) {
                        Some(&s) => (self.route_cells[s].clone(), self.route_edges[s].clone()),
                        None => (Vec::new(), Vec::new()),
                    }
                });
                let inb = |c: Cell| cell_in_box(c, gx0, gy0, gx1, gy1, gc);
                // cells: keep cur's out-of-box cells, take this box's in-box cells
                let mut cells: Vec<Cell> =
                    cur_cells.into_iter().filter(|c| !inb(*c)).collect();
                cells.extend(nw_cells.into_iter().filter(|c| inb(*c)));
                cells.sort_unstable();
                cells.dedup();
                // edges: keep cur's non-(in-in-box) edges, take this box's in-in-box edges
                let mut edges: Vec<Edge> = cur_edges
                    .into_iter()
                    .filter(|(a, b)| !(inb(*a) && inb(*b)))
                    .collect();
                edges.extend(nw_edges.into_iter().filter(|(a, b)| inb(*a) && inb(*b)));
                merged.insert(net, (cells, edges));
            }
        }
        merged
            .into_iter()
            .map(|(net, (c, e))| (net, c, e))
            .collect()
    }
}

impl Grid {
    /// Stage 3c-1b/3c-2: the FlexDR worker (port of Python `route_box`) for one box,
    /// run entirely in Rust, mutating ONLY worker-local state -> &self, so a
    /// checkerboard batch of these can run in parallel reading the shared grid +
    /// global occ read-only (OpenROAD FlexDRWorker model). Reads the persistent
    /// routes store + global occ + the constant worker inputs; keeps a LOCAL route
    /// overlay + a LOCAL occ signed-delta (instead of mutating the shared occ);
    /// drives the queue rip-up/reroute via `maze_run` + `worker_markers`. Returns
    /// `(delta, local_occ, accept)`: the changed-net delta `[(net_id, new_cells,
    /// new_edges)]`, the local occ delta to merge on accept, and whether the box is
    /// accepted. The caller (route_box / route_boxes) does the SERIAL merge
    /// (occ_merge_local) + applies the delta to the store. BYTE-PARITY with the
    /// Python route_box for the canonical (sorted-pins) reference.
    #[allow(clippy::too_many_arguments)]
    fn route_box_plan(
        &self,
        gx0: i32,
        gy0: i32,
        gx1: i32,
        gy1: i32,
        gc: i32, // GC (gcell size in cells)
        wire_halo: i32,
        via_halo: i32,
        ripup_all: bool, // true = ALL mode, false = DRC
        maze_end_iter: i32,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        marker_decay: f64,
        prl_halo: i32,
        prl_len: i32,
        vertical: &[bool],
    ) -> (Vec<(i32, Vec<Cell>, Vec<Edge>)>, HashMap<i64, HashMap<i32, i32>>, bool) {
        // box cell bounds + drc region + corridor (faithful to route_box top)
        let rx0 = gx0 * gc;
        let ry0 = gy0 * gc;
        let rx1 = ((gx1 + 1) * gc - 1).min(self.nx - 1);
        let ry1 = ((gy1 + 1) * gc - 1).min(self.ny - 1);
        let dm = wire_halo.max(via_halo).max(prl_halo) + 1;
        let region = Some((
            (rx0 - dm).max(0),
            (ry0 - dm).max(0),
            (rx1 + dm).min(self.nx - 1),
            (ry1 + dm).min(self.ny - 1),
        ));
        let mut corridor_gkeys: HashSet<i64> = HashSet::new();
        for gx in gx0..=gx1 {
            for gy in gy0..=gy1 {
                corridor_gkeys.insert(gx as i64 * 2_000_000i64 + gy as i64);
            }
        }

        let mut local_occ: HashMap<i64, HashMap<i32, i32>> = HashMap::new();
        let mut overlay: Overlay = HashMap::new();
        let mut marker_count: HashMap<i64, f64> = HashMap::new();
        let empty_set: HashSet<i64> = HashSet::new();

        // init markers (seed marker cost, FlexDR_maze.cpp:1751)
        let init_markers =
            self.worker_markers(&overlay, wire_halo, via_halo, region, prl_halo, prl_len, vertical);
        let init_n = init_markers.len();
        for m in &init_markers {
            for c in &m.0 {
                *marker_count.entry(self.idx(c.0, c.1, c.2)).or_insert(0.0) += 1.0;
            }
        }

        let mut num_reroute: HashMap<i32, i32> = HashMap::new();
        let mut queue: VecDeque<i32> = VecDeque::new();
        let mut queued: HashSet<i32> = HashSet::new();

        // initial queue
        if ripup_all {
            // sorted nets in-box with >= 2 terminals
            let mut init: Vec<i32> = Vec::new();
            for &nm in &self.route_net {
                let nterms = self.term_sets.get(&nm).map(|t| t.len()).unwrap_or(0);
                if nterms >= 2 && self.net_in_box(nm, &overlay, gx0, gy0, gx1, gy1, gc) {
                    init.push(nm);
                }
            }
            init.sort_by_key(|&n| self.rank(n));
            for nm in init {
                self.try_enqueue(
                    nm, maze_end_iter, gx0, gy0, gx1, gy1, gc, &overlay, &num_reroute,
                    &mut queue, &mut queued,
                );
            }
        } else {
            // DRC: canonical sorted SET of movable marker sources
            let mut srcs: HashSet<i32> = HashSet::new();
            for m in &init_markers {
                for &s in &m.2 {
                    if !self.supply_ids.contains(&s) {
                        srcs.insert(s);
                    }
                }
            }
            let mut srcv: Vec<i32> = srcs.into_iter().collect();
            srcv.sort_by_key(|&n| self.rank(n));
            for nm in srcv {
                self.try_enqueue(
                    nm, maze_end_iter, gx0, gy0, gx1, gy1, gc, &overlay, &num_reroute,
                    &mut queue, &mut queued,
                );
            }
        }

        while let Some(nm) = queue.pop_front() {
            queued.remove(&nm);
            // can_ripup
            if self.supply_ids.contains(&nm)
                || *num_reroute.get(&nm).unwrap_or(&0) >= maze_end_iter
            {
                continue;
            }
            *num_reroute.entry(nm).or_insert(0) += 1;

            // current route (clone so no borrow is held across the occ mutation)
            let (cur_cells, cur_edges): (Vec<Cell>, Vec<Edge>) = match overlay.get(&nm) {
                Some((c, e)) => (c.clone(), e.clone()),
                None => match self.slot_of_net.get(&nm) {
                    Some(&s) => (self.route_cells[s].clone(), self.route_edges[s].clone()),
                    None => continue,
                },
            };
            let terminals: Vec<Vec<Cell>> =
                self.term_sets.get(&nm).cloned().unwrap_or_default();
            let (keep_cells, keep_edges, targets) =
                self.extract_targets(&cur_cells, &cur_edges, &terminals, gx0, gy0, gx1, gy1, gc);

            // hard = obstacles + every supply net's footprint (occ feeds foreign signal)
            let mut hard_set: HashSet<i64> = self.obstacles.clone();
            let supply_ids: Vec<i32> = self.supply_ids.iter().cloned().collect();
            for snm in supply_ids {
                if let Some(&s) = self.slot_of_net.get(&snm) {
                    let cells = self.route_cells[s].clone();
                    let via = self.route_via[s].clone();
                    self.net_footprint_into(&cells, &via, wire_halo, via_halo, &mut hard_set);
                }
            }
            let marker_set: HashSet<i64> = marker_count
                .iter()
                .filter(|(_, &v)| v >= 0.5)
                .map(|(&k, _)| k)
                .collect();

            let routed = self.connect_targets(
                nm, &targets, &hard_set, &empty_set, &marker_set, &empty_set, gg_drc, gg_marker,
                gg_fixed, &corridor_gkeys, gc, true, Some(&local_occ),
            );
            let (tree, ne) = match routed {
                Some(x) => x,
                None => continue, // keep old route for this net
            };

            // new route = sorted(keep_cells | tree); edges = keep_edges + ne
            let mut new_cells: Vec<Cell> = keep_cells.union(&tree).cloned().collect();
            new_cells.sort_unstable();
            let mut new_edges = keep_edges;
            new_edges.extend(ne);
            let new_via = via_from_edges(&new_edges);
            let old_via = via_from_edges(&cur_edges);

            overlay.insert(nm, (new_cells.clone(), new_edges));
            // keep the LOCAL occ in sync with this reroute (remove old, add new)
            self.occ_apply_local(&mut local_occ, nm, &cur_cells, &old_via, false);
            self.occ_apply_local(&mut local_occ, nm, &new_cells, &new_via, true);

            let ms = self
                .worker_markers(&overlay, wire_halo, via_halo, region, prl_halo, prl_len, vertical);
            for m in &ms {
                for c in &m.0 {
                    *marker_count.entry(self.idx(c.0, c.1, c.2)).or_insert(0.0) += 1.0;
                }
            }
            // requeue: canonical sorted SET of movable sources
            let mut req: HashSet<i32> = HashSet::new();
            for m in &ms {
                for &s in &m.2 {
                    if !self.supply_ids.contains(&s) {
                        req.insert(s);
                    }
                }
            }
            let mut reqv: Vec<i32> = req.into_iter().collect();
            reqv.sort_by_key(|&n| self.rank(n));
            for src in reqv {
                self.try_enqueue(
                    src, maze_end_iter, gx0, gy0, gx1, gy1, gc, &overlay, &num_reroute,
                    &mut queue, &mut queued,
                );
            }
            // decay
            for v in marker_count.values_mut() {
                *v *= marker_decay;
            }
            if ms.is_empty() {
                break;
            }
        }

        // commit (FlexDR_end.cpp:682): accept iff final <= limit and something changed
        let final_n = self
            .worker_markers(&overlay, wire_halo, via_halo, region, prl_halo, prl_len, vertical)
            .len();
        let limit = if ripup_all { 5 * init_n } else { init_n };
        let mut changed: Vec<i32> = Vec::new();
        for (&nm, (nc, _ne)) in &overlay {
            let same = self
                .slot_of_net
                .get(&nm)
                .map(|&s| &self.route_cells[s] == nc)
                .unwrap_or(false);
            if !same {
                changed.push(nm);
            }
        }
        let accept = final_n <= limit && !changed.is_empty();
        if !accept {
            return (Vec::new(), local_occ, false);
        }
        let mut out: Vec<(i32, Vec<Cell>, Vec<Edge>)> = Vec::with_capacity(changed.len());
        for nm in changed {
            if let Some((c, e)) = overlay.get(&nm) {
                out.push((nm, c.clone(), e.clone()));
            }
        }
        (out, local_occ, true)
    }
}

impl Grid {
    /// Faithful A* (spec section 3) over pre-built cost sets -- the shared kernel
    /// behind the `box_maze` pymethod and the in-Rust worker. `starts`/`goals` are
    /// cell slices; the cost sets are idx sets; `corridor_gkeys` is the box
    /// corridor as packed gcell keys (start/goal gcells are added here, matching
    /// box_maze). Returns the cell path or None. Byte-parity tie-break unchanged.
    #[allow(clippy::too_many_arguments)]
    fn maze_run(
        &self,
        net: i32,
        starts: &[Cell],
        goals: &[Cell],
        hard_set: &HashSet<i64>,
        route_set: &HashSet<i64>,
        marker_set: &HashSet<i64>,
        fixed_set: &HashSet<i64>,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        corridor_gkeys: &HashSet<i64>,
        gc: i32,
        use_occ: bool,
        local_occ: Option<&HashMap<i64, HashMap<i32, i32>>>,
    ) -> Option<Vec<Cell>> {
        let cell_legal = |ix: i32, iy: i32, l: i32| -> bool {
            self.in_bounds(ix, iy)
                && l >= 0
                && l < self.nz
                && self.wire_ok(ix, iy, l, net)
                && !hard_set.contains(&self.idx(ix, iy, l))
        };

        // legal goals/starts (cells)
        let mut legal_goals: HashSet<i64> = HashSet::new();
        let mut goal_list: Vec<Cell> = Vec::new();
        for c in goals {
            if cell_legal(c.0, c.1, c.2) && legal_goals.insert(self.idx(c.0, c.1, c.2)) {
                goal_list.push(*c);
            }
        }
        let legal_starts: Vec<Cell> = starts
            .iter()
            .filter(|c| cell_legal(c.0, c.1, c.2))
            .cloned()
            .collect();
        if legal_goals.is_empty() || legal_starts.is_empty() {
            return None;
        }

        // allowed gcells = corridor + gcells of starts and goals
        let gkey = |gx: i32, gy: i32| -> i64 { gx as i64 * 2_000_000i64 + gy as i64 };
        let mut allowed: HashSet<i64> =
            HashSet::with_capacity(corridor_gkeys.len() + legal_starts.len() + goal_list.len());
        allowed.extend(corridor_gkeys.iter().cloned());
        for c in legal_starts.iter().chain(goal_list.iter()) {
            allowed.insert(gkey(c.0 / gc, c.1 / gc));
        }

        let heur = |ix: i32, iy: i32, l: i32| -> f64 {
            let mut best = i64::MAX;
            for (gx, gy, gl) in &goal_list {
                let d = (ix - gx).abs() as i64
                    + (iy - gy).abs() as i64
                    + if l == *gl { 0 } else { 2 };
                if d < best {
                    best = d;
                }
            }
            best as f64
        };

        let state_key = |cell_idx: i64, dir: u8| -> i64 { cell_idx * 7 + dir as i64 };

        let mut best: HashMap<i64, f64> = HashMap::new();
        let mut came: HashMap<i64, i64> = HashMap::new();
        let mut done: HashSet<i64> = HashSet::new();
        let mut heap: BinaryHeap<HeapItem> = BinaryHeap::new();

        for c in &legal_starts {
            let ci = self.idx(c.0, c.1, c.2);
            let sk = state_key(ci, D_NONE);
            best.insert(sk, 0.0);
            heap.push(HeapItem {
                f: heur(c.0, c.1, c.2),
                cost: 0.0,
                ix: c.0,
                iy: c.1,
                layer: c.2,
                dir: D_NONE,
                state: sk,
            });
        }

        while let Some(item) = heap.pop() {
            if done.contains(&item.state) {
                continue;
            }
            done.insert(item.state);
            let (cix, ciy, cl, d) = (item.ix, item.iy, item.layer, item.dir);
            let cell_idx = self.idx(cix, ciy, cl);
            if legal_goals.contains(&cell_idx) {
                return Some(self.reconstruct(&came, item.state));
            }
            let cost = item.cost;

            // ---- neighbors (planar 4-dir then vias), faithful to _neighbors ----
            const STEPS: [(i32, i32); 4] = [(1, 0), (-1, 0), (0, 1), (0, -1)];
            for (dx, dy) in STEPS {
                let (nx, ny) = (cix + dx, ciy + dy);
                if self.in_bounds(nx, ny) && self.wire_ok(nx, ny, cl, net) {
                    self.relax(
                        net, cix, ciy, cl, d, cost, nx, ny, cl, 0.0, gc, &allowed,
                        &legal_goals, hard_set, route_set, marker_set, fixed_set, gg_drc,
                        gg_marker, gg_fixed, use_occ, local_occ, &heur, &state_key, &mut best,
                        &mut came, &mut heap,
                    );
                }
            }
            // vias
            if !self.via_blocked.contains(&(ciy as i64 * self.nx as i64 + cix as i64))
                && self.wire_ok(cix, ciy, cl, net)
            {
                for (a, b, vcost) in &self.via_index {
                    let other = if cl == *a {
                        *b
                    } else if cl == *b {
                        *a
                    } else {
                        continue;
                    };
                    if self.wire_ok(cix, ciy, other, net) {
                        self.relax(
                            net, cix, ciy, cl, d, cost, cix, ciy, other, *vcost, gc, &allowed,
                            &legal_goals, hard_set, route_set, marker_set, fixed_set, gg_drc,
                            gg_marker, gg_fixed, use_occ, local_occ, &heur, &state_key,
                            &mut best, &mut came, &mut heap,
                        );
                    }
                }
            }
        }
        None
    }

    /// The faithful FlexGC-lite computation, shared by the marshaled `flexgc`
    /// (pass-boundary gc) and the worker's in-place `worker_markers`. `nets` yields
    /// (net_id, route_cells, via_cells) in routes-dict order; it is iterated TWICE
    /// (cover pass, then prl owner pass) so the iterator must be Clone.
    ///
    /// BYTE-PARITY contract: the returned marker LIST ORDER + the owner/cover
    /// first-touch assignment must match Python `flexgc_lite` (marker order
    /// changes routes). Cover + prl `runs` are INSERTION-ORDERED maps
    /// (Vec of keys + HashMap index), reproducing Python dict first-touch exactly.
    /// `vertical` indexed by layer: true = preferred dir "V". `region` =
    /// (cx0,cy0,cx1,cy1) or None. Markers come back as (cells, layer, source ids);
    /// source order is irrelevant (route_box sorts by NAME).
    #[allow(clippy::too_many_arguments)]
    fn flexgc_core<'a, I>(
        &self,
        nets: I,
        wire_halo: i32,
        via_halo: i32,
        region: Option<(i32, i32, i32, i32)>,
        prl_halo: i32,
        prl_len: i32,
        vertical: &[bool],
    ) -> Vec<(Vec<(i32, i32, i32)>, i32, Vec<i32>)>
    where
        I: Iterator<Item = (i32, &'a [(i32, i32, i32)], &'a [(i32, i32, i32)])> + Clone,
    {
        let in_region = |ix: i32, iy: i32| -> bool {
            match region {
                None => true,
                Some((x0, y0, x1, y1)) => x0 <= ix && ix <= x1 && y0 <= iy && iy <= y1,
            }
        };

        // ---- cover: cell -> set of net ids, in FIRST-TOUCH insertion order ----
        let mut order: Vec<i64> = Vec::new(); // cell_idx in first-touch order
        let mut pos: HashMap<i64, usize> = HashMap::new();
        let mut coords: Vec<(i32, i32, i32)> = Vec::new();
        let mut srcs: Vec<Vec<i32>> = Vec::new();
        for (nid, cells, via_cells) in nets.clone() {
            let mut via_set: HashSet<i64> = HashSet::with_capacity(via_cells.len());
            for (ix, iy, l) in via_cells {
                via_set.insert(self.idx(*ix, *iy, *l));
            }
            for (ix, iy, l) in cells {
                let halo = if via_set.contains(&self.idx(*ix, *iy, *l)) {
                    via_halo
                } else {
                    wire_halo
                };
                let mut dx = -halo;
                while dx <= halo {
                    let mut dy = -halo;
                    while dy <= halo {
                        let (nx, ny) = (ix + dx, iy + dy);
                        if self.in_bounds(nx, ny) && in_region(nx, ny) {
                            let f = self.idx(nx, ny, *l);
                            match pos.get(&f) {
                                Some(&p) => {
                                    if !srcs[p].contains(&nid) {
                                        srcs[p].push(nid);
                                    }
                                }
                                None => {
                                    pos.insert(f, order.len());
                                    order.push(f);
                                    coords.push((nx, ny, *l));
                                    srcs.push(vec![nid]);
                                }
                            }
                        }
                        dy += 1;
                    }
                    dx += 1;
                }
            }
        }
        let mut out: Vec<(Vec<(i32, i32, i32)>, i32, Vec<i32>)> = Vec::new();
        for p in 0..coords.len() {
            if srcs[p].len() > 1 {
                let c = coords[p];
                out.push((vec![c], c.2, srcs[p].clone()));
            }
        }

        // ---- parallel-run-length markers ----
        if prl_halo > 0 && prl_len > 0 {
            // owner: first net to touch a cell wins, in first-touch insertion order
            let mut owner_id: HashMap<i64, i32> = HashMap::new();
            let mut owner_order: Vec<(i32, i32, i32, i32)> = Vec::new(); // (ix,iy,li,owner)
            for (nid, cells, _via) in nets {
                for (ix, iy, l) in cells {
                    let key = self.idx(*ix, *iy, *l);
                    if !owner_id.contains_key(&key) {
                        owner_id.insert(key, nid);
                        owner_order.push((*ix, *iy, *l, nid));
                    }
                }
            }
            // runs: insertion-ordered keys -> set of run-direction coords
            type RunKey = (i32, bool, i32, i32, i32, i32); // (li, vert, s0, s1, pair_lo, pair_hi)
            let mut run_pos: HashMap<RunKey, usize> = HashMap::new();
            let mut run_keys: Vec<RunKey> = Vec::new();
            let mut run_coords: Vec<HashSet<i32>> = Vec::new();
            for (ix, iy, li, a) in &owner_order {
                let vert = *vertical.get(*li as usize).unwrap_or(&false);
                let run_c = if vert { *iy } else { *ix };
                let sep = if vert { *ix } else { *iy };
                for k in 1..=prl_halo {
                    let (c2x, c2y) = if vert { (ix + k, *iy) } else { (*ix, iy + k) };
                    if !self.in_bounds(c2x, c2y) {
                        continue; // Python tuple-key never aliases; Rust idx would -> guard
                    }
                    if let Some(&b) = owner_id.get(&self.idx(c2x, c2y, *li)) {
                        if b != *a {
                            let (lo, hi) = if *a < b { (*a, b) } else { (b, *a) };
                            let key = (*li, vert, sep, sep + k, lo, hi);
                            let p = match run_pos.get(&key) {
                                Some(&p) => p,
                                None => {
                                    let p = run_keys.len();
                                    run_pos.insert(key, p);
                                    run_keys.push(key);
                                    run_coords.push(HashSet::new());
                                    p
                                }
                            };
                            run_coords[p].insert(run_c);
                        }
                    }
                }
            }
            for p in 0..run_keys.len() {
                let (li, vert, s0, s1, lo, hi) = run_keys[p];
                let mut cs: Vec<i32> = run_coords[p].iter().cloned().collect();
                cs.sort_unstable();
                let mut i = 0usize;
                while i < cs.len() {
                    let mut j = i;
                    while j + 1 < cs.len() && cs[j + 1] == cs[j] + 1 {
                        j += 1;
                    }
                    if (j - i + 1) >= prl_len as usize {
                        let mut cells: Vec<(i32, i32, i32)> = Vec::new();
                        let mut seen: HashSet<i64> = HashSet::new();
                        for s_idx in i..=j {
                            let rc = cs[s_idx];
                            for s in [s0, s1] {
                                let cell = if vert { (s, rc, li) } else { (rc, s, li) };
                                if in_region(cell.0, cell.1)
                                    && seen.insert(self.idx(cell.0, cell.1, cell.2))
                                {
                                    cells.push(cell);
                                }
                            }
                        }
                        if !cells.is_empty() {
                            out.push((cells, li, vec![lo, hi]));
                        }
                    }
                    i = j + 1;
                }
            }
        }
        out
    }

    #[allow(clippy::too_many_arguments)]
    fn relax(
        &self,
        net: i32,
        cix: i32,
        ciy: i32,
        cl: i32,
        d: u8,
        cost: f64,
        nx: i32,
        ny: i32,
        nl: i32,
        extra: f64,
        gc: i32,
        allowed: &HashSet<i64>,
        legal_goals: &HashSet<i64>,
        hard_set: &HashSet<i64>,
        route_set: &HashSet<i64>,
        marker_set: &HashSet<i64>,
        fixed_set: &HashSet<i64>,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        use_occ: bool,
        local_occ: Option<&HashMap<i64, HashMap<i32, i32>>>,
        heur: &dyn Fn(i32, i32, i32) -> f64,
        state_key: &dyn Fn(i64, u8) -> i64,
        best: &mut HashMap<i64, f64>,
        came: &mut HashMap<i64, i64>,
        heap: &mut BinaryHeap<HeapItem>,
    ) {
        let nidx = self.idx(nx, ny, nl);
        let gkey = (nx / gc) as i64 * 2_000_000i64 + (ny / gc) as i64;
        if !legal_goals.contains(&nidx) && !allowed.contains(&gkey) {
            return;
        }
        // _cell_legal(nxt): in_bounds + wire_ok + not hard
        if !(self.in_bounds(nx, ny)
            && nl >= 0
            && nl < self.nz
            && self.wire_ok(nx, ny, nl, net)
            && !hard_set.contains(&nidx))
        {
            return;
        }
        let nd = dir_rank(cl, nl, nx - cix, ny - ciy);
        let mut step = 1.0;
        if d != D_NONE && nd != d {
            step += 1.0;
        }
        if nl != cl {
            step += extra;
        }
        let foreign = if use_occ {
            match local_occ {
                None => self.occ_has_foreign(nidx, net),
                Some(l) => self.occ_has_foreign_ov(l, nidx, net),
            }
        } else {
            route_set.contains(&nidx)
        };
        if foreign {
            step += gg_drc;
        }
        if marker_set.contains(&nidx) {
            step += gg_marker;
        }
        if fixed_set.contains(&nidx) {
            step += gg_fixed;
        }
        let ncost = cost + step;
        let ns = state_key(nidx, nd);
        let prev = *best.get(&ns).unwrap_or(&f64::INFINITY);
        if ncost + 1e-12 < prev {
            best.insert(ns, ncost);
            came.insert(ns, state_key(self.idx(cix, ciy, cl), d));
            heap.push(HeapItem {
                f: ncost + heur(nx, ny, nl),
                cost: ncost,
                ix: nx,
                iy: ny,
                layer: nl,
                dir: nd,
                state: ns,
            });
        }
    }

    fn reconstruct(&self, came: &HashMap<i64, i64>, end_state: i64) -> Vec<(i32, i32, i32)> {
        let mut states = vec![end_state];
        while let Some(&prev) = came.get(states.last().unwrap()) {
            states.push(prev);
        }
        states.reverse();
        states
            .into_iter()
            .map(|sk| {
                let cell_idx = sk / 7;
                let layer = (cell_idx / self.plane) as i32;
                let rem = cell_idx % self.plane;
                let iy = (rem / self.nx as i64) as i32;
                let ix = (rem % self.nx as i64) as i32;
                (ix, iy, layer)
            })
            .collect()
    }

    // --- Stage 3c-1b: in-Rust worker (route_box) helpers ----------------------

    /// NAME-sorted rank of a net id (the worker queue order key); unknown -> MAX.
    #[inline]
    fn rank(&self, nm: i32) -> i32 {
        *self.name_rank.get(&nm).unwrap_or(&i32::MAX)
    }

    /// A net's current cell list within the box: overlay (this box's reroute) if
    /// present, else the persistent store. None if the net is unknown.
    fn net_cells<'a>(&'a self, nm: i32, overlay: &'a Overlay) -> Option<&'a [Cell]> {
        if let Some((c, _)) = overlay.get(&nm) {
            Some(c.as_slice())
        } else {
            self.slot_of_net.get(&nm).map(|&s| self.route_cells[s].as_slice())
        }
    }

    /// True if any of net `nm`'s cells lie in the worker box.
    #[allow(clippy::too_many_arguments)]
    fn net_in_box(
        &self,
        nm: i32,
        overlay: &Overlay,
        gx0: i32,
        gy0: i32,
        gx1: i32,
        gy1: i32,
        gc: i32,
    ) -> bool {
        match self.net_cells(nm, overlay) {
            None => false,
            Some(cells) => cells
                .iter()
                .any(|c| cell_in_box(*c, gx0, gy0, gx1, gy1, gc)),
        }
    }

    /// Expand a net's halo footprint into `out` (idx set) -- faithful to Python
    /// `_net_footprint`: each cell gets via_halo if it is a via cell, else wire_halo.
    fn net_footprint_into(
        &self,
        cells: &[Cell],
        via: &[Cell],
        wire_halo: i32,
        via_halo: i32,
        out: &mut HashSet<i64>,
    ) {
        let mut via_set: HashSet<i64> = HashSet::with_capacity(via.len());
        for c in via {
            via_set.insert(self.idx(c.0, c.1, c.2));
        }
        for c in cells {
            let halo = if via_set.contains(&self.idx(c.0, c.1, c.2)) {
                via_halo
            } else {
                wire_halo
            };
            let mut dx = -halo;
            while dx <= halo {
                let mut dy = -halo;
                while dy <= halo {
                    let (nx, ny) = (c.0 + dx, c.1 + dy);
                    if self.in_bounds(nx, ny) {
                        out.insert(self.idx(nx, ny, c.2));
                    }
                    dy += 1;
                }
                dx += 1;
            }
        }
    }

    /// Port of `extract_box` + the `_route_net_in_box` target list. Splits net
    /// `nm`'s route around the box: keep_cells (out-of-box + boundary pins),
    /// keep_edges, and the connect targets = SORTED pin singletons (canonical,
    /// 3c-1b-0) + in-box terminal sets.
    #[allow(clippy::too_many_arguments)]
    fn extract_targets(
        &self,
        cells: &[Cell],
        edges: &[Edge],
        terminals: &[Vec<Cell>],
        gx0: i32,
        gy0: i32,
        gx1: i32,
        gy1: i32,
        gc: i32,
    ) -> (HashSet<Cell>, Vec<Edge>, Vec<Vec<Cell>>) {
        let inb = |c: Cell| cell_in_box(c, gx0, gy0, gx1, gy1, gc);
        let mut keep_cells: HashSet<Cell> =
            cells.iter().filter(|c| !inb(**c)).cloned().collect();
        let mut keep_edges: Vec<Edge> = Vec::new();
        let mut pins: HashSet<Cell> = HashSet::new();
        for (a, b) in edges {
            let (ia, ib) = (inb(*a), inb(*b));
            if !ia && !ib {
                keep_edges.push((*a, *b));
            } else if ia && ib {
                // ripped interior edge -> dropped
            } else {
                pins.insert(if ia { *a } else { *b });
                keep_edges.push((*a, *b));
            }
        }
        keep_cells.extend(pins.iter().cloned());
        // targets = sorted pin singletons (canonical) + non-empty in-box terminals
        let mut pin_sorted: Vec<Cell> = pins.into_iter().collect();
        pin_sorted.sort_unstable();
        let mut targets: Vec<Vec<Cell>> = pin_sorted.into_iter().map(|p| vec![p]).collect();
        for t in terminals {
            if !t.is_empty() && t.iter().any(|c| inb(*c)) {
                targets.push(t.clone());
            }
        }
        (keep_cells, keep_edges, targets)
    }

    /// Port of `_connect_targets`: connect the target cell-sets into one tree via
    /// `maze_run` (target0->target1, then each remaining to the growing tree),
    /// SORTING starts each call to match Python. Returns (tree cells, edges) or
    /// None if a connection fails. <2 targets -> nothing to route.
    #[allow(clippy::too_many_arguments)]
    fn connect_targets(
        &self,
        net: i32,
        targets: &[Vec<Cell>],
        hard_set: &HashSet<i64>,
        route_set: &HashSet<i64>,
        marker_set: &HashSet<i64>,
        fixed_set: &HashSet<i64>,
        gg_drc: f64,
        gg_marker: f64,
        gg_fixed: f64,
        corridor_gkeys: &HashSet<i64>,
        gc: i32,
        use_occ: bool,
        local_occ: Option<&HashMap<i64, HashMap<i32, i32>>>,
    ) -> Option<(HashSet<Cell>, Vec<Edge>)> {
        if targets.len() < 2 {
            return Some((HashSet::new(), Vec::new()));
        }
        let t0: HashSet<Cell> = targets[0].iter().cloned().collect();
        let t1: HashSet<Cell> = targets[1].iter().cloned().collect();
        let mut tree: HashSet<Cell>;
        let mut edges: Vec<Edge> = Vec::new();
        if t0.intersection(&t1).next().is_some() {
            tree = t0.union(&t1).cloned().collect();
        } else {
            let mut starts: Vec<Cell> = targets[0].clone();
            starts.sort_unstable();
            let path = self.maze_run(
                net, &starts, &targets[1], hard_set, route_set, marker_set, fixed_set,
                gg_drc, gg_marker, gg_fixed, corridor_gkeys, gc, use_occ, local_occ,
            )?;
            edges = path.windows(2).map(|w| (w[0], w[1])).collect();
            tree = path.into_iter().collect();
        }
        for tgt in &targets[2..] {
            let tgt_set: HashSet<Cell> = tgt.iter().cloned().collect();
            if tree.intersection(&tgt_set).next().is_some() {
                continue;
            }
            let mut starts: Vec<Cell> = tree.iter().cloned().collect();
            starts.sort_unstable();
            let path = self.maze_run(
                net, &starts, tgt, hard_set, route_set, marker_set, fixed_set, gg_drc,
                gg_marker, gg_fixed, corridor_gkeys, gc, use_occ, local_occ,
            )?;
            for w in path.windows(2) {
                edges.push((w[0], w[1]));
            }
            tree.extend(path);
        }
        Some((tree, edges))
    }

    /// flexgc over the store + the box's `overlay` (changed nets), restricted to
    /// `region`: reads the persistent routes store in place and overlays only the
    /// nets this box rerouted (cells+edges) -> byte-parity with flexgc over the
    /// box's local R, without re-marshaling all routes. Returns markers (cells,
    /// layer, source net ids).
    #[allow(clippy::too_many_arguments)]
    fn worker_markers(
        &self,
        overlay: &Overlay,
        wire_halo: i32,
        via_halo: i32,
        region: Option<(i32, i32, i32, i32)>,
        prl_halo: i32,
        prl_len: i32,
        vertical: &[bool],
    ) -> Vec<(Vec<Cell>, i32, Vec<i32>)> {
        // pre-derive overlay via so the flexgc iterator can borrow it
        let over: HashMap<i32, (&[Cell], Vec<Cell>)> = overlay
            .iter()
            .map(|(nid, (c, e))| (*nid, (c.as_slice(), via_from_edges(e))))
            .collect();
        let iter = (0..self.route_net.len()).map(|s| {
            let nid = self.route_net[s];
            match over.get(&nid) {
                Some((c, v)) => (nid, *c, v.as_slice()),
                None => (
                    nid,
                    self.route_cells[s].as_slice(),
                    self.route_via[s].as_slice(),
                ),
            }
        });
        self.flexgc_core(iter, wire_halo, via_halo, region, prl_halo, prl_len, vertical)
    }

    /// enqueue(nm) guard == Python: skip if already queued, if supply or rerouted
    /// `maze_end_iter` times (can_ripup), or if not in the box.
    #[allow(clippy::too_many_arguments)]
    fn try_enqueue(
        &self,
        nm: i32,
        maze_end_iter: i32,
        gx0: i32,
        gy0: i32,
        gx1: i32,
        gy1: i32,
        gc: i32,
        overlay: &Overlay,
        num_reroute: &HashMap<i32, i32>,
        queue: &mut VecDeque<i32>,
        queued: &mut HashSet<i32>,
    ) {
        if queued.contains(&nm)
            || self.supply_ids.contains(&nm)
            || *num_reroute.get(&nm).unwrap_or(&0) >= maze_end_iter
            || !self.net_in_box(nm, overlay, gx0, gy0, gx1, gy1, gc)
        {
            return;
        }
        queued.insert(nm);
        queue.push_back(nm);
    }
}

#[pymodule]
fn klink_boxmaze_rs(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Grid>()?;
    Ok(())
}
