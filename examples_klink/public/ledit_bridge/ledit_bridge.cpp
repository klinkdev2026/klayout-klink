// ============================================================================
// ledit_bridge.cpp — klink <-> L-Edit file-exchange RPC bridge  (v0.1.0)
//
// Architecture (deliberately boring, replaces the v15 socket design):
//   - ONE thread: the L-Edit UI thread. A SetTimer callback polls an inbox
//     directory every 200 ms. No worker threads, no message queues, no
//     helper DLLs. All UPI calls happen on the UI thread by construction.
//   - Transport: JSON files. Agent writes  <ns>\inbox\req_<id>.json,
//     bridge writes <ns>\outbox\resp_<id>.json (atomic: .tmp + rename).
//     <ns> = %LOCALAPPDATA%\klink\ledit_bridge\default
//   - Extensibility: one command = one handler function + one row in
//     g_commands[]. Nothing else to touch.
//
// Request : {"schema":1, "id":"req_xxx", "cmd":"ping", "params":{...}}
// Response: {"schema":1, "id":"req_xxx", "ok":true,  "result":{...}}
//         | {"schema":1, "id":"req_xxx", "ok":false,
//            "error":{"code":"...", "message":"...", "next_action":"..."}}
//
// Commands v0.1 (units: ALL coordinates/sizes in microns, doubles):
//   ping            -> {proto, macro_version, cell, microns_per_intu}
//   get_layers      -> {layers:[{name, gds_layer, gds_datatype}]}
//   ensure_layer    {name, gds_layer?, gds_datatype?} -> {created:bool}
//   create_cell     {name} -> {created:bool}   (existing cell = ok, created:false)
//   draw            {cell?, items:[
//                      {kind:"box",     layer, bbox_um:[x0,y0,x1,y1]}
//                      {kind:"polygon", layer, points_um:[[x,y],...]}
//                      {kind:"wire",    layer, points_um:[[x,y],...], width_um,
//                                       cap?:"butt"|"round"|"extend"}
//                      {kind:"circle",  layer, center_um:[x,y], radius_um}
//                    ]} -> {drawn, layers_created}
//   get_selection   -> {cell, count, objects:[...], skipped:{kind:count}}
//   place_instance  {cell?, child, x_um, y_um, orient?, mirror_x?,
//                    nx?, ny?, dx_um?, dy_um?} -> {placed:true}
//   set_layer_style {layer, fill_rgb?:[r,g,b], fill?:"solid"|"none",
//                    outline_rgb?:[r,g,b]} -> {fill_rgb_used, ...}
//                   (colors snap to the nearest design-palette entry)
//   get_cell        {cell} -> full inventory: shapes (same forms as
//                   get_selection) + instances (name/master/is_tcell/
//                   transform/repeat/properties). T-Cell harvesting entry.
//   clear_cell      {cell} -> delete ALL shapes+instances in that cell.
//                   cell is REQUIRED (the visible cell is never cleared
//                   implicitly).
//   list_cells      -> {cells:[{name, is_tcell, hidden?}]}
//   instance_tcell  {cell?, tcell, params:{name:value}, x_um?, y_um?}
//                   -> generated instance (incl. the auto-variant cell name)
//   set_tcell_code  {cell, code, language?=5} -> EXPERIMENTAL write of
//                   T-Cell generator source into System.TCell Code
//   new_design      {name?, setup_from_visible?} -> create a fresh .tdb
//                   (works with NO design open; setup_from_visible
//                   inherits the open design's technology/layers)
//   open_design     {path} -> open an existing .tdb
//   save_design     {path?} -> Save (or SaveAs when path given)
//   list_designs    -> {designs:[{file, visible, changed, cells}], count}
//                   every command targets the VISIBLE design; this is how a
//                   caller sees the others
//   activate_design {file} -> switch between ALREADY-OPEN designs by name
//                   (no disk path, so unsaved new_design output is
//                   reachable); raises an existing window when there is
//                   one, so nothing inside the design is touched
//   import_gds      {path, overwrite?:"all"|"top"|"none", use_gds_datatype?,
//                   log_path?} -> {cells_before, cells_after, cells_added,
//                   log_path}. Bulk/hierarchical transfer: GDS keeps the
//                   CELL HIERARCHY, ignores the request cap, and
//                   overwrite:"all" makes re-import idempotent (draw only
//                   appends). Incremental/parametric work stays on the RPC
//                   path.
//   batch           {ops:[{cmd, params}, ...]} -> {results:[...], completed}
//                   N commands, ONE request, in order, stopping at the
//                   first failure. The transport costs ~one poll interval
//                   per REQUEST regardless of size, so batching (or writing
//                   independent requests as a pipeline) is what makes bulk
//                   work fast -- not smaller payloads.
//   get_tcell_params {cell, names:[...]} -> per-name default/previous
//   get_drc_rules   -> the design's full DRC rule table (type, layers,
//                   distance) - the tdb's process knowledge
//
// v0.5 readout depth: every converted object carries its property tree
// (dotted names = nesting); wires report cap/join; torus/pie report exact
// params; get_cell adds ports[] + labels[]; get_layers adds fill_rgb/fill
// + layer properties.
//
// v0.2 selection/get_cell semantics: instances are serialized (not
// skipped); unknown shape kinds with a vertex outline degrade to
// kind:"polygon" + source_kind; only outline-less kinds are counted in
// skipped{kind:count}. ping/hello report the .tdb file name so users can
// confirm the bridge is attached to the right design.
//
// v0.5.1 design-targeting safety (blind-test findings): new_design /
// open_design now activate the new file and FAIL if it could not be made
// the visible design (v0.5.0 could leave the user's design active and
// silently receive all writes); open_design pre-checks the path (a failed
// LFile_Open pops a modal dialog, which freezes the bridge timer) and
// falls back to path+".tdb"; every file-bound command echoes result.file;
// optional params.expect_file refuses to touch any other design.
//
// Load (no separate compile step — L-Edit builds source macros itself):
//   L-Edit: Tools > Macro > Load Macro... -> select THIS .cpp file.
//   The bridge starts polling automatically on load; Tools menu gets
//   "klink: Bridge Status" / "klink: Bridge Stop" / "klink: Bridge Start".
//
// API discipline: old-version subset only (v9.30/Ex99/Ex830-era calls)
// for maximum version reach. Verified on Tanner v16.3; older versions are
// compatible BY DESIGN but untested - report issues if one rejects it.
// Known v0.1 limits (by design, round 2 material): no TTL/idempotency
// journal reload across L-Edit restarts, no chunking (>4 MiB responses),
// single fixed namespace "default".
// ============================================================================

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <string>
#include <vector>
#include <set>
#include <utility>

#include <ldata.h>   // L-Edit UPI (user-supplied SDK include path)

#define BRIDGE_PROTO         1
#define BRIDGE_MACRO_VERSION "0.5.5"
// Adaptive poll: measured cost of one round trip was ~0.20 s, essentially
// all of it poll latency (payload is nearly free -- 400 boxes drew in
// 0.16 s). Poll fast for a burst window after any activity, idle slowly.
#define POLL_INTERVAL_MS     15      // hot: back-to-back calls cost ~this
#define POLL_IDLE_MS         200     // cold: nothing seen for a while
#define POLL_HOT_TICKS       120     // stay hot ~1.8 s after the last request
#define HELLO_EVERY_MS       2000    // refresh hello.json every ~2 s
#define SWEEP_EVERY_MS       300000  // reap uncollected responses every 5 min
#define REQ_CAP_BYTES        (64 * 1024)

// v15.cpp-proven export style for source-loaded macros: a plain extern "C"
// declaration block; definitions carry no decoration. No __declspec.
extern "C" {
    void klink_bridge_start(void);
    void klink_bridge_stop(void);
    void klink_bridge_status(void);
    int  UPI_Entry_Point(void);
}

// ----------------------------------------------------------------------------
// Minimal JSON value + parser + serializer (C++03, no dependencies).
// Supports: null, bool, number(double), string(UTF-8), array, object.
// ----------------------------------------------------------------------------

// Encoding helpers (defined below, used by the JSON layer): L-Edit is
// ANSI, JSON is UTF-8.
static std::wstring to_wide(const std::string& s, UINT cp);
static std::string  from_wide(const std::wstring& w, UINT cp);

struct JVal {
    enum T { JNULL, JBOOL, JNUM, JSTR, JARR, JOBJ };
    T t;
    bool b;
    double num;
    std::string str;
    std::vector<JVal> arr;
    std::vector<std::pair<std::string, JVal> > obj;

    JVal() : t(JNULL), b(false), num(0.0) {}

    static JVal N(double d)              { JVal v; v.t = JNUM; v.num = d; return v; }
    static JVal B(bool x)                { JVal v; v.t = JBOOL; v.b = x; return v; }
    static JVal S(const std::string& s)  { JVal v; v.t = JSTR; v.str = s; return v; }
    static JVal A()                      { JVal v; v.t = JARR; return v; }
    static JVal O()                      { JVal v; v.t = JOBJ; return v; }

    void set(const std::string& k, const JVal& v) {
        obj.push_back(std::make_pair(k, v));
    }
    const JVal* get(const char* k) const {
        for (size_t i = 0; i < obj.size(); ++i)
            if (obj[i].first == k) return &obj[i].second;
        return 0;
    }
    double num_or(const char* k, double def) const {
        const JVal* v = get(k);
        return (v && v->t == JNUM) ? v->num : def;
    }
    std::string str_or(const char* k, const char* def) const {
        const JVal* v = get(k);
        return (v && v->t == JSTR) ? v->str : std::string(def);
    }
    bool bool_or(const char* k, bool def) const {
        const JVal* v = get(k);
        return (v && v->t == JBOOL) ? v->b : def;
    }
};

class JParser {
public:
    JParser(const std::string& s) : s_(s), p_(0), ok_(true) {}
    bool parse(JVal& out) {
        skip();
        parse_value(out);
        skip();
        return ok_ && p_ == s_.size();
    }
private:
    const std::string& s_;
    size_t p_;
    bool ok_;

    void fail() { ok_ = false; p_ = s_.size(); }
    int  peek() { return p_ < s_.size() ? (unsigned char)s_[p_] : -1; }
    int  next() { return p_ < s_.size() ? (unsigned char)s_[p_++] : -1; }
    void skip() {
        while (p_ < s_.size()) {
            char c = s_[p_];
            if (c == ' ' || c == '\t' || c == '\r' || c == '\n') ++p_;
            else break;
        }
    }
    bool lit(const char* w) {
        size_t n = strlen(w);
        if (s_.compare(p_, n, w) == 0) { p_ += n; return true; }
        fail(); return false;
    }
    void parse_value(JVal& v) {
        if (!ok_) return;
        switch (peek()) {
            case '{': parse_obj(v); break;
            case '[': parse_arr(v); break;
            case '"': v.t = JVal::JSTR; parse_str(v.str); break;
            case 't': if (lit("true"))  { v.t = JVal::JBOOL; v.b = true;  } break;
            case 'f': if (lit("false")) { v.t = JVal::JBOOL; v.b = false; } break;
            case 'n': if (lit("null"))  { v.t = JVal::JNULL; } break;
            default:  parse_num(v); break;
        }
    }
    void parse_obj(JVal& v) {
        v.t = JVal::JOBJ; next(); skip();
        if (peek() == '}') { next(); return; }
        for (;;) {
            std::string key;
            skip();
            if (peek() != '"') { fail(); return; }
            parse_str(key);
            skip();
            if (next() != ':') { fail(); return; }
            skip();
            JVal child;
            parse_value(child);
            if (!ok_) return;
            v.obj.push_back(std::make_pair(key, child));
            skip();
            int c = next();
            if (c == ',') continue;
            if (c == '}') return;
            fail(); return;
        }
    }
    void parse_arr(JVal& v) {
        v.t = JVal::JARR; next(); skip();
        if (peek() == ']') { next(); return; }
        for (;;) {
            JVal child;
            skip();
            parse_value(child);
            if (!ok_) return;
            v.arr.push_back(child);
            skip();
            int c = next();
            if (c == ',') continue;
            if (c == ']') return;
            fail(); return;
        }
    }
    // Accumulates UTF-8 (raw bytes and \uXXXX alike), then hands back ANSI:
    // every consumer passes these straight into L-Edit's ANSI API, so a
    // UTF-8 path would reach fopen/LFile_Open as the wrong bytes and simply
    // not be found.
    void parse_str(std::string& out) {
        std::string u8;
        parse_str_utf8(u8);
        out = from_wide(to_wide(u8, CP_UTF8), CP_ACP);
        if (out.empty() && !u8.empty()) out = u8;   // unconvertible: keep raw
    }

    void parse_str_utf8(std::string& out) {
        out.clear();
        if (next() != '"') { fail(); return; }
        for (;;) {
            int c = next();
            if (c < 0) { fail(); return; }
            if (c == '"') return;
            if (c == '\\') {
                int e = next();
                switch (e) {
                    case '"':  out += '"';  break;
                    case '\\': out += '\\'; break;
                    case '/':  out += '/';  break;
                    case 'b':  out += '\b'; break;
                    case 'f':  out += '\f'; break;
                    case 'n':  out += '\n'; break;
                    case 'r':  out += '\r'; break;
                    case 't':  out += '\t'; break;
                    case 'u': {
                        unsigned int cp = 0;
                        for (int i = 0; i < 4; ++i) {
                            int h = next();
                            cp <<= 4;
                            if (h >= '0' && h <= '9') cp |= (h - '0');
                            else if (h >= 'a' && h <= 'f') cp |= (h - 'a' + 10);
                            else if (h >= 'A' && h <= 'F') cp |= (h - 'A' + 10);
                            else { fail(); return; }
                        }
                        // encode BMP code point as UTF-8 (surrogates unsupported)
                        if (cp < 0x80) out += (char)cp;
                        else if (cp < 0x800) {
                            out += (char)(0xC0 | (cp >> 6));
                            out += (char)(0x80 | (cp & 0x3F));
                        } else {
                            out += (char)(0xE0 | (cp >> 12));
                            out += (char)(0x80 | ((cp >> 6) & 0x3F));
                            out += (char)(0x80 | (cp & 0x3F));
                        }
                        break;
                    }
                    default: fail(); return;
                }
            } else {
                out += (char)c;
            }
        }
    }
    void parse_num(JVal& v) {
        size_t start = p_;
        if (peek() == '-') next();
        while (peek() >= '0' && peek() <= '9') next();
        if (peek() == '.') { next(); while (peek() >= '0' && peek() <= '9') next(); }
        if (peek() == 'e' || peek() == 'E') {
            next();
            if (peek() == '+' || peek() == '-') next();
            while (peek() >= '0' && peek() <= '9') next();
        }
        if (p_ == start) { fail(); return; }
        v.t = JVal::JNUM;
        v.num = atof(s_.substr(start, p_ - start).c_str());
    }
};

// ---------------------------------------------------------------------------
// Text encoding. L-Edit's API is ANSI (the system codepage), while JSON is
// UTF-8 by definition. Passing ANSI bytes through produced files that were
// NOT valid UTF-8 as soon as a design lived under a non-ASCII path (a Chinese
// OneDrive folder, say): every reader raised a decode error that looked like
// a bridge crash. Convert at the boundary in both directions, and emit pure
// ASCII (\uXXXX) so the wire format is immune to whatever codepage either
// side is running.
// ---------------------------------------------------------------------------
static std::wstring to_wide(const std::string& s, UINT cp) {
    if (s.empty()) return std::wstring();
    int n = MultiByteToWideChar(cp, 0, s.c_str(), (int)s.size(), NULL, 0);
    if (n <= 0) return std::wstring();
    std::wstring w((size_t)n, L'\0');
    MultiByteToWideChar(cp, 0, s.c_str(), (int)s.size(), &w[0], n);
    return w;
}

static std::string from_wide(const std::wstring& w, UINT cp) {
    if (w.empty()) return std::string();
    int n = WideCharToMultiByte(cp, 0, w.c_str(), (int)w.size(),
                                NULL, 0, NULL, NULL);
    if (n <= 0) return std::string();
    std::string s((size_t)n, '\0');
    WideCharToMultiByte(cp, 0, w.c_str(), (int)w.size(), &s[0], n, NULL, NULL);
    return s;
}

static void jesc(const std::string& in, std::string& out) {
    out += '"';
    // ANSI in -> wide -> ASCII-only JSON out.
    std::wstring w = to_wide(in, CP_ACP);
    if (w.empty() && !in.empty()) {          // conversion refused: never drop
        for (size_t i = 0; i < in.size(); ++i) {
            char buf[8];
            sprintf(buf, "\\u%04x", (int)(unsigned char)in[i]);
            out += buf;
        }
        out += '"';
        return;
    }
    for (size_t i = 0; i < w.size(); ++i) {
        unsigned int c = (unsigned int)w[i];
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\b': out += "\\b";  break;
            case '\f': out += "\\f";  break;
            case '\n': out += "\\n";  break;
            case '\r': out += "\\r";  break;
            case '\t': out += "\\t";  break;
            default:
                if (c < 0x20 || c > 0x7E) {
                    char buf[8];
                    sprintf(buf, "\\u%04x", c);
                    out += buf;
                } else {
                    out += (char)c;
                }
        }
    }
    out += '"';
}

static void jdump(const JVal& v, std::string& out) {
    char buf[64];
    switch (v.t) {
        case JVal::JNULL: out += "null"; break;
        case JVal::JBOOL: out += v.b ? "true" : "false"; break;
        case JVal::JNUM:
            if (v.num == (double)(long long)v.num &&
                v.num > -1e15 && v.num < 1e15)
                sprintf(buf, "%lld", (long long)v.num);
            else
                sprintf(buf, "%.10g", v.num);
            out += buf;
            break;
        case JVal::JSTR: jesc(v.str, out); break;
        case JVal::JARR:
            out += '[';
            for (size_t i = 0; i < v.arr.size(); ++i) {
                if (i) out += ',';
                jdump(v.arr[i], out);
            }
            out += ']';
            break;
        case JVal::JOBJ:
            out += '{';
            for (size_t i = 0; i < v.obj.size(); ++i) {
                if (i) out += ',';
                jesc(v.obj[i].first, out);
                out += ':';
                jdump(v.obj[i].second, out);
            }
            out += '}';
            break;
    }
}

// ----------------------------------------------------------------------------
// Bridge state + filesystem plumbing
// ----------------------------------------------------------------------------

static UINT_PTR g_timerId = 0;
static bool     g_inTick  = false;          // re-entrancy guard
static unsigned g_tick    = 0;
// Adaptive polling state: the timer always fires at POLL_INTERVAL_MS, but a
// COLD bridge only scans the inbox every POLL_IDLE_MS worth of ticks, so an
// idle L-Edit keeps the old cheap duty cycle while a busy one answers ~13x
// sooner. Retuning the timer itself is not an option: a NULL-hwnd timer
// cannot be reset by id, only killed and recreated.
static unsigned g_hotTicks    = 0;          // >0 while requests are flowing
static DWORD    g_lastHelloMs = 0;
static DWORD    g_lastSweepMs = 0;

static void write_hello();                  // defined with the polling loop
static std::string g_root, g_inbox, g_outbox, g_logPath;
static std::set<std::string> g_processed;   // in-memory idempotency (v0.1)

static void bridge_log(const char* msg) {
    if (!g_logPath.empty()) {
        FILE* f = fopen(g_logPath.c_str(), "a");
        if (f) {
            SYSTEMTIME st; GetLocalTime(&st);
            fprintf(f, "%04d-%02d-%02d %02d:%02d:%02d.%03d %s\n",
                    st.wYear, st.wMonth, st.wDay,
                    st.wHour, st.wMinute, st.wSecond, st.wMilliseconds, msg);
            fclose(f);
        }
    }
    LUpi_LogMessage(msg);   // also to L-Edit's own log window
}

static bool ensure_dir(const std::string& path) {
    if (CreateDirectoryA(path.c_str(), NULL)) return true;
    return GetLastError() == ERROR_ALREADY_EXISTS;
}

static bool init_paths() {
    char buf[MAX_PATH] = {0};
    std::string p;

    // KLINK_LEDIT_BRIDGE_ROOT is the escape hatch for callers that cannot
    // write LOCALAPPDATA (sandboxes, redirected profiles). The klink client
    // has honoured it all along; the macro did not, so the two ends pointed
    // at different directories and the hatch silently did nothing.
    DWORD n = GetEnvironmentVariableA("KLINK_LEDIT_BRIDGE_ROOT", buf, MAX_PATH);
    if (n > 0 && n < MAX_PATH) {
        p = buf;
        ensure_dir(p);
    } else {
        n = GetEnvironmentVariableA("LOCALAPPDATA", buf, MAX_PATH);
        std::string base;
        if (n > 0 && n < MAX_PATH) base = buf;
        else base = "C:\\klink_bridge";      // fallback if env unavailable
        // build the tree piece by piece (no recursive mkdir on WinAPI)
        p = base;
        ensure_dir(p);
        p += "\\klink";        if (!ensure_dir(p)) return false;
        p += "\\ledit_bridge"; if (!ensure_dir(p)) return false;
    }
    p += "\\default";         if (!ensure_dir(p)) return false;
    g_root   = p;
    g_inbox  = p + "\\inbox";  if (!ensure_dir(g_inbox))  return false;
    g_outbox = p + "\\outbox"; if (!ensure_dir(g_outbox)) return false;
    g_logPath = p + "\\bridge.log";
    return true;
}

static bool read_file(const std::string& path, std::string& out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (sz < 0 || sz > REQ_CAP_BYTES) { fclose(f); return false; }  // request cap
    out.resize((size_t)sz);
    size_t rd = sz ? fread(&out[0], 1, (size_t)sz, f) : 0;
    fclose(f);
    return rd == (size_t)sz;
}

static long file_size(const std::string& path) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return -1;
    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fclose(f);
    return sz;
}

// Pull "id":"..." out of the head of a request we are not going to parse
// (an oversized one). Without the real id the response filename would not
// match what the caller waits for, and it would time out anyway.
static bool sniff_request_id(const std::string& path, std::string& out) {
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) return false;
    char buf[1024];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = 0;
    std::string head(buf, n);
    size_t k = head.find("\"id\"");
    if (k == std::string::npos) return false;
    k = head.find('"', head.find(':', k) + 1);
    if (k == std::string::npos) return false;
    size_t e = head.find('"', k + 1);
    if (e == std::string::npos) return false;
    out = head.substr(k + 1, e - k - 1);
    return !out.empty();
}

// atomic publish: write .tmp, then rename into place
static bool write_file_atomic(const std::string& path, const std::string& data) {
    std::string tmp = path + ".tmp";
    FILE* f = fopen(tmp.c_str(), "wb");
    if (!f) return false;
    size_t wr = data.empty() ? 0 : fwrite(&data[0], 1, data.size(), f);
    fclose(f);
    if (wr != data.size()) { DeleteFileA(tmp.c_str()); return false; }
    if (!MoveFileExA(tmp.c_str(), path.c_str(), MOVEFILE_REPLACE_EXISTING)) {
        DeleteFileA(tmp.c_str());
        return false;
    }
    return true;
}

static void append_processed(const std::string& id) {
    g_processed.insert(id);
    FILE* f = fopen((g_root + "\\processed.jsonl").c_str(), "a");
    if (f) { fprintf(f, "{\"id\":\"%s\"}\n", id.c_str()); fclose(f); }
}

// ----------------------------------------------------------------------------
// UPI helpers (units, layers, cells)
// ----------------------------------------------------------------------------

struct Ctx {                 // resolved once per request
    LFile file;
    LCell cell;              // target cell (visible cell unless params say else)
};

static LCoord um2i(LFile f, double um) { return LFile_MicronsToIntU(f, um); }
static double i2um(LFile f, LCoord v)  { return LFile_IntUtoMicrons(f, v); }

// default style for BRIDGE-CREATED layers: deterministic palette color by
// name hash, HATCHED fill, NO outline (owner ruling: borderless fills keep
// the selection highlight legible). Existing layers are never restyled.
// 8x8 stipples for auto-styled layers. NOT solid: a layout is read by
// seeing THROUGH the stack, and solid fill on every layer hides exactly the
// overlap that tells you a contact lands on its metal. Each pattern leaves
// most pixels clear, and picking pattern AND colour by the layer name gives
// far more separable layers than colour alone.
static unsigned hash_name(const std::string& name) {
    unsigned h = 5381;
    for (size_t i = 0; i < name.size(); ++i)
        h = h * 33 + (unsigned char)name[i];
    return h;
}

static const unsigned char AUTO_STIPPLES[8][8] = {
    {0x11, 0x22, 0x44, 0x88, 0x11, 0x22, 0x44, 0x88},  // diagonal /
    {0x88, 0x44, 0x22, 0x11, 0x88, 0x44, 0x22, 0x11},  // diagonal backslash
    {0x99, 0x66, 0x66, 0x99, 0x99, 0x66, 0x66, 0x99},  // cross-hatch
    {0xFF, 0x00, 0x00, 0x00, 0xFF, 0x00, 0x00, 0x00},  // horizontal lines
    {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11},  // vertical lines
    {0x22, 0x00, 0x88, 0x00, 0x22, 0x00, 0x88, 0x00},  // sparse dots
    {0xFF, 0x11, 0x11, 0x11, 0xFF, 0x11, 0x11, 0x11},  // grid
    {0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA, 0x55, 0xAA},  // 50% weave
};

static void auto_style_new_layer(LFile file, LLayer layer,
                                 const std::string& name) {
    LRenderingAttribute ra;
    memset(&ra, 0, sizeof(ra));
    if (LLayer_GetRenderingAttribute(layer, raiObject, &ra) != LStatusOK)
        return;
    int n = LFile_GetColorPaletteNumColors(file);
    if (n <= 1) return;
    unsigned h = hash_name(name);
    unsigned color = 1 + (h % (unsigned)(n - 1));      // skip entry 0
    ra.mFillColorIndex = color;
    memcpy(ra.mFillPattern, AUTO_STIPPLES[(h >> 5) % 8], sizeof(LStipple));
    // Outline stays OFF by earlier ruling (borderless fills keep the
    // selection highlight legible); pass outline_rgb to set_layer_style to
    // opt in per layer.
    memset(ra.mOutlinePattern, 0x00, sizeof(LStipple));
    LLayer_SetRenderingAttribute(layer, raiObject, &ra);
}

// find layer by name; optionally create it and stamp GDS numbers
static LLayer ensure_layer_impl(LFile file, const std::string& name,
                                int gdsLayer, int gdsType, bool* created) {
    if (created) *created = false;
    LLayer layer = LLayer_Find(file, name.c_str());
    if (!layer) {
        LLayer_New(file, NULL, name.c_str());
        layer = LLayer_Find(file, name.c_str());
        if (created && layer) {
            *created = true;
            auto_style_new_layer(file, layer, name);
        }
    }
    if (layer && gdsLayer >= 0) {
        LLayerParamEx830 lp;
        memset(&lp, 0, sizeof(lp));
        if (LLayer_GetParametersEx830(layer, &lp) == LStatusOK) {
            lp.GDSNumber   = (short)gdsLayer;
            lp.GDSDataType = (short)(gdsType >= 0 ? gdsType : 0);
            LLayer_SetParametersEx830(layer, &lp);
        }
    }
    return layer;
}

static std::string layer_name_of(LLayer layer) {
    char buf[256] = {0};
    LLayer_GetName(layer, buf, sizeof(buf) - 1);
    return std::string(buf);
}

static std::string cell_name_of(LCell cell) {
    char buf[256] = {0};
    LCell_GetName(cell, buf, sizeof(buf) - 1);
    return std::string(buf);
}

static std::string file_name_of(LFile file) {
    char buf[512] = {0};
    LFile_GetName(file, buf, sizeof(buf) - 1);
    return std::string(buf);
}

static const char* shape_kind_name(LShapeType t) {
    switch (t) {
        case LBox:         return "box";
        case LCircle:      return "circle";
        case LWire:        return "wire";
        case LPolygon:     return "polygon";
        case LTorus:       return "torus";
        case LPie:         return "pie";
        case LObjInstance: return "instance";
        case LObjPort:     return "port";
        case LObjRuler:    return "ruler";
        case LObjLabel:    return "label";
        case LVia:         return "via";
        default:           return "other";
    }
}

// special-layer detection via the API (never by name matching)
static bool is_special_layer(LFile file, LLayer layer) {
    static const LSpecialLayer kSpecials[] = {
        GridLayer, OriginLayer, CellOutlineLayer, ErrorLayer,
        IconLayer, DragBoxLayer,
    };
    for (size_t i = 0; i < sizeof(kSpecials) / sizeof(kSpecials[0]); ++i)
        if (LLayer_GetSpecial(file, kSpecials[i]) == layer) return true;
    return false;
}

// enumerate an entity's properties into a JSON object (T-Cell parameters
// surface here); unsupported value types are reported, not dropped
static JVal props_to_json(LEntity entity) {
    JVal out = JVal::O();
    for (const char* name = LEntity_GetFirstProperty(entity); name;
         name = LEntity_GetNextProperty()) {
        LPropertyType type = L_unassigned;
        if (LEntity_GetPropertyType(entity, name, &type) != LStatusOK)
            continue;
        if (type == L_string) {
            unsigned int sz = LEntity_GetPropertyValueSize(entity, name);
            if (sz > 65536) { out.set(name, JVal::S("<oversized string>")); continue; }
            std::vector<char> buf((size_t)sz + 2, 0);
            if (LEntity_GetPropertyValue(entity, name, &buf[0], sz + 1) == LStatusOK)
                out.set(name, JVal::S(std::string(&buf[0])));
        } else if (type == L_int) {
            long v = 0;
            if (LEntity_GetPropertyValue(entity, name, &v, sizeof(v)) == LStatusOK)
                out.set(name, JVal::N((double)v));
        } else if (type == L_real) {
            double v = 0.0;
            if (LEntity_GetPropertyValue(entity, name, &v, sizeof(v)) == LStatusOK)
                out.set(name, JVal::N(v));
        } else if (type == L_bool) {
            long v = 0;
            if (LEntity_GetPropertyValue(entity, name, &v, sizeof(v)) == LStatusOK)
                out.set(name, JVal::B(v != 0));
        } else {
            char note[48];
            sprintf(note, "<unsupported property type %d>", (int)type);
            out.set(name, JVal::S(note));
        }
    }
    return out;
}

static void bump(JVal& counter, const std::string& key) {
    for (size_t i = 0; i < counter.obj.size(); ++i)
        if (counter.obj[i].first == key) {
            counter.obj[i].second.num += 1;
            return;
        }
    counter.set(key, JVal::N(1));
}

// ----------------------------------------------------------------------------
// Command handlers.
// Contract: return true + fill result, or return false + fill err/next_action.
// ----------------------------------------------------------------------------

typedef bool (*CmdHandler)(Ctx&, const JVal& params, JVal& result,
                           std::string& err, std::string& next);

// expect_file matching: unsaved designs report a bare name, saved designs a
// full path — accept an exact match or a trailing path component match.
static bool file_matches(const std::string& active, const std::string& expect) {
    if (_stricmp(active.c_str(), expect.c_str()) == 0) return true;
    if (expect.size() < active.size()) {
        size_t off = active.size() - expect.size();
        char sep = active[off - 1];
        if ((sep == '\\' || sep == '/') &&
            _stricmp(active.c_str() + off, expect.c_str()) == 0) return true;
    }
    return false;
}

static bool resolve_ctx(const JVal& params, Ctx& ctx, bool needs_file,
                        bool needs_cell,
                        std::string& err, std::string& next) {
    ctx.file = LFile_GetVisible();
    ctx.cell = 0;
    if (!ctx.file) {
        if (!needs_file) return true;   // e.g. ping/new_design on fresh L-Edit
        err  = "no visible design file in L-Edit";
        next = "open a .tdb in L-Edit, or call new_design to create one";
        return false;
    }
    // Optional guard: refuse to touch a design the caller did not intend.
    // A failed/forgotten new_design otherwise silently redirects every write
    // into whatever design happens to be active (e.g. the user's).
    std::string expect = params.str_or("expect_file", "");
    if (needs_file && !expect.empty()) {
        std::string cur = file_name_of(ctx.file);
        if (!file_matches(cur, expect)) {
            err  = "active design is '" + cur + "' but expect_file is '" +
                   expect + "'";
            next = "call activate_design {file:\"" + expect + "\"} to switch "
                   "back (it works on any OPEN design, saved or not), then "
                   "retry; list_designs shows what is open";
            return false;
        }
    }
    std::string cellName = params.str_or("cell", "");
    if (cellName.empty()) {
        ctx.cell = LCell_GetVisible();
        if (!ctx.cell && needs_cell) {
            err  = "no visible cell";
            next = "click into a layout window (or open a cell), or pass params.cell";
            return false;
        }
    } else {
        ctx.cell = LCell_Find(ctx.file, cellName.c_str());
        // a create-if-missing command (needs_cell=false) resolves its own
        // target; only cell-consuming commands hard-fail here
        if (!ctx.cell && needs_cell) {
            err  = "cell not found: " + cellName;
            next = "call create_cell first, or check get_selection.cell for the exact name";
            return false;
        }
    }
    return true;
}

static bool cmd_ping(Ctx& ctx, const JVal&, JVal& result,
                     std::string&, std::string&) {
    result.set("proto", JVal::N(BRIDGE_PROTO));
    result.set("macro_version", JVal::S(BRIDGE_MACRO_VERSION));
    result.set("file", JVal::S(ctx.file ? file_name_of(ctx.file)
                                        : std::string("")));
    result.set("cell", JVal::S(ctx.cell ? cell_name_of(ctx.cell)
                                        : std::string("")));
    result.set("design_ready", JVal::B(ctx.file != 0));
    if (ctx.file)
        result.set("microns_per_intu", JVal::N(i2um(ctx.file, 1)));
    JVal caps = JVal::A();
    caps.arr.push_back(JVal::S("ping"));
    caps.arr.push_back(JVal::S("get_layers"));
    caps.arr.push_back(JVal::S("ensure_layer"));
    caps.arr.push_back(JVal::S("create_cell"));
    caps.arr.push_back(JVal::S("draw"));
    caps.arr.push_back(JVal::S("get_selection"));
    caps.arr.push_back(JVal::S("place_instance"));
    caps.arr.push_back(JVal::S("set_layer_style"));
    caps.arr.push_back(JVal::S("get_cell"));
    caps.arr.push_back(JVal::S("clear_cell"));
    caps.arr.push_back(JVal::S("list_cells"));
    caps.arr.push_back(JVal::S("instance_tcell"));
    caps.arr.push_back(JVal::S("set_tcell_code"));
    caps.arr.push_back(JVal::S("new_design"));
    caps.arr.push_back(JVal::S("open_design"));
    caps.arr.push_back(JVal::S("save_design"));
    caps.arr.push_back(JVal::S("get_tcell_params"));
    caps.arr.push_back(JVal::S("get_drc_rules"));
    caps.arr.push_back(JVal::S("batch"));
    caps.arr.push_back(JVal::S("list_designs"));
    caps.arr.push_back(JVal::S("activate_design"));
    caps.arr.push_back(JVal::S("import_gds"));
    result.set("capabilities", caps);
    return true;
}

static bool cmd_get_layers(Ctx& ctx, const JVal&, JVal& result,
                           std::string&, std::string&) {
    JVal layers = JVal::A();
    for (LLayer layer = LLayer_GetList(ctx.file); layer;
         layer = LLayer_GetNext(layer)) {
        JVal e = JVal::O();
        e.set("name", JVal::S(layer_name_of(layer)));
        LLayerParamEx830 lp;
        memset(&lp, 0, sizeof(lp));
        if (LLayer_GetParametersEx830(layer, &lp) == LStatusOK) {
            e.set("gds_layer", JVal::N(lp.GDSNumber));
            e.set("gds_datatype", JVal::N(lp.GDSDataType));
        }
        e.set("special", JVal::B(is_special_layer(ctx.file, layer)));
        // display style: fill color resolved through the design palette
        LRenderingAttribute ra;
        memset(&ra, 0, sizeof(ra));
        if (LLayer_GetRenderingAttribute(layer, raiObject, &ra) == LStatusOK) {
            LColor col;
            memset(&col, 0, sizeof(col));
            if (LFile_GetColorPalette(ctx.file, &col,
                                      (int)ra.mFillColorIndex) == LStatusOK) {
                JVal rgb = JVal::A();
                rgb.arr.push_back(JVal::N(col.LRed));
                rgb.arr.push_back(JVal::N(col.LGreen));
                rgb.arr.push_back(JVal::N(col.LBlue));
                e.set("fill_rgb", rgb);
            }
            bool solid = true, none = true;
            for (int k = 0; k < 8; ++k) {
                if (ra.mFillPattern[k] != 0xFF) solid = false;
                if (ra.mFillPattern[k] != 0x00) none = false;
            }
            e.set("fill", JVal::S(solid ? "solid" : none ? "none" : "stipple"));
        }
        JVal lprops = props_to_json((LEntity)layer);
        if (!lprops.obj.empty()) e.set("properties", lprops);
        layers.arr.push_back(e);
    }
    result.set("layers", layers);
    return true;
}

static bool cmd_ensure_layer(Ctx& ctx, const JVal& params, JVal& result,
                             std::string& err, std::string& next) {
    std::string name = params.str_or("name", "");
    if (name.empty()) {
        err  = "params.name is required";
        next = "pass {\"name\":\"Metal1\", \"gds_layer\":1, \"gds_datatype\":0}";
        return false;
    }
    int gdsLayer = (int)params.num_or("gds_layer", -1);
    int gdsType  = (int)params.num_or("gds_datatype", -1);
    bool created = false;
    LLayer layer = ensure_layer_impl(ctx.file, name, gdsLayer, gdsType, &created);
    if (!layer) {
        err  = "could not create layer: " + name;
        next = "check the name for characters L-Edit rejects, or create it manually once";
        return false;
    }
    result.set("created", JVal::B(created));
    return true;
}

static bool cmd_create_cell(Ctx& ctx, const JVal& params, JVal& result,
                            std::string& err, std::string& next) {
    std::string name = params.str_or("name", "");
    if (name.empty()) {
        err  = "params.name is required";
        next = "pass {\"name\":\"klink_gen_1\"}";
        return false;
    }
    LCell existing = LCell_Find(ctx.file, name.c_str());
    if (existing) {
        result.set("created", JVal::B(false));
        return true;
    }
    LCell cell = LCell_New(ctx.file, name.c_str());
    if (!cell) {
        err  = "LCell_New failed for: " + name;
        next = "check for duplicate/illegal cell name in this design";
        return false;
    }
    result.set("created", JVal::B(true));
    return true;
}

// read [[x,y],...] (µm) into an LPoint vector.
// NOTE: LPoint's struct layout is {y,x} — always build via LPoint_Set(x,y).
static bool read_points(Ctx& ctx, const JVal* pts, std::vector<LPoint>& out) {
    if (!pts || pts->t != JVal::JARR || pts->arr.size() < 1) return false;
    for (size_t i = 0; i < pts->arr.size(); ++i) {
        const JVal& p = pts->arr[i];
        if (p.t != JVal::JARR || p.arr.size() != 2 ||
            p.arr[0].t != JVal::JNUM || p.arr[1].t != JVal::JNUM) return false;
        out.push_back(LPoint_Set(um2i(ctx.file, p.arr[0].num),
                                 um2i(ctx.file, p.arr[1].num)));
    }
    return true;
}

// Resolving the layer per ITEM meant one LLayer_Find (plus a GDS parameter
// write) for every shape: a 6000-shape push across 5 layers paid 6000
// lookups for 5 distinct answers. Cache them for the life of one request.
// First mention wins, so a later item repeating the same layer NAME with
// different GDS numbers does not re-stamp it -- pass numbers consistently,
// or call ensure_layer explicitly.
struct LayerCache {
    LFile file;
    std::vector<std::pair<std::string, LLayer> > seen;
    int created;
    LayerCache(LFile f) : file(f), created(0) {}
    LLayer get(const std::string& name, int gl, int gd) {
        for (size_t i = 0; i < seen.size(); ++i)
            if (seen[i].first == name) return seen[i].second;
        bool made = false;
        LLayer l = ensure_layer_impl(file, name, gl, gd, &made);
        if (made) ++created;
        if (l) seen.push_back(std::make_pair(name, l));
        return l;
    }
};

static bool cmd_draw(Ctx& ctx, const JVal& params, JVal& result,
                     std::string& err, std::string& next) {
    const JVal* items = params.get("items");
    if (!items || items->t != JVal::JARR || items->arr.empty()) {
        err  = "params.items must be a non-empty array";
        next = "pass items:[{\"kind\":\"box\",\"layer\":\"Metal1\",\"bbox_um\":[0,0,10,10]}]";
        return false;
    }
    int drawn = 0;
    LayerCache lc(ctx.file);
    for (size_t i = 0; i < items->arr.size(); ++i) {
        const JVal& it = items->arr[i];
        char where[64];
        sprintf(where, "items[%d]", (int)i);
        std::string kind      = it.str_or("kind", "");
        std::string layerName = it.str_or("layer", "");
        if (layerName.empty()) {
            err  = std::string(where) + ": layer is required";
            next = "every draw item needs a layer name; see get_layers";
            return false;
        }
        LLayer layer = lc.get(layerName,
                              (int)it.num_or("gds_layer", -1),
                              (int)it.num_or("gds_datatype", -1));
        if (!layer) {
            err  = std::string(where) + ": cannot find/create layer " + layerName;
            next = "check layer name; call get_layers for the current table";
            return false;
        }

        if (kind == "box") {
            const JVal* bb = it.get("bbox_um");
            if (!bb || bb->t != JVal::JARR || bb->arr.size() != 4) {
                err  = std::string(where) + ": box needs bbox_um:[x0,y0,x1,y1]";
                next = "fix the item and resend the draw request";
                return false;
            }
            if (!LBox_New(ctx.cell, layer,
                          um2i(ctx.file, bb->arr[0].num),
                          um2i(ctx.file, bb->arr[1].num),
                          um2i(ctx.file, bb->arr[2].num),
                          um2i(ctx.file, bb->arr[3].num))) {
                err  = std::string(where) + ": LBox_New failed";
                next = "check bbox coordinates (x0<x1, y0<y1) and layer lock state";
                return false;
            }
        } else if (kind == "polygon") {
            std::vector<LPoint> pts;
            if (!read_points(ctx, it.get("points_um"), pts) || pts.size() < 3) {
                err  = std::string(where) + ": polygon needs points_um:[[x,y],...] with >=3 points";
                next = "fix the item and resend the draw request";
                return false;
            }
            if (!LPolygon_New(ctx.cell, layer, &pts[0], (int)pts.size())) {
                err  = std::string(where) + ": LPolygon_New failed";
                next = "check for self-intersecting outline or a locked layer";
                return false;
            }
        } else if (kind == "wire") {
            std::vector<LPoint> pts;
            if (!read_points(ctx, it.get("points_um"), pts) || pts.size() < 2) {
                err  = std::string(where) + ": wire needs points_um:[[x,y],...] with >=2 points";
                next = "fix the item and resend the draw request";
                return false;
            }
            double widthUm = it.num_or("width_um", 0.0);
            if (widthUm <= 0.0) {
                err  = std::string(where) + ": wire needs width_um > 0";
                next = "fix the item and resend the draw request";
                return false;
            }
            LWireConfig cfg;
            memset(&cfg, 0, sizeof(cfg));
            cfg.width = um2i(ctx.file, widthUm);
            std::string cap = it.str_or("cap", "butt");
            cfg.cap = (cap == "round")  ? LCapRound  :
                      (cap == "extend") ? LCapExtend : LCapButt;
            if (!LWire_New(ctx.cell, layer, &cfg,
                           LSetWireWidth | LSetWireCap,
                           &pts[0], (int)pts.size())) {
                err  = std::string(where) + ": LWire_New failed";
                next = "check points/width and layer lock state";
                return false;
            }
        } else if (kind == "circle") {
            const JVal* c = it.get("center_um");
            double r = it.num_or("radius_um", 0.0);
            if (!c || c->t != JVal::JARR || c->arr.size() != 2 || r <= 0.0) {
                err  = std::string(where) + ": circle needs center_um:[x,y] and radius_um>0";
                next = "fix the item and resend the draw request";
                return false;
            }
            if (!LCircle_New(ctx.cell, layer,
                             LPoint_Set(um2i(ctx.file, c->arr[0].num),
                                        um2i(ctx.file, c->arr[1].num)),
                             um2i(ctx.file, r))) {
                err  = std::string(where) + ": LCircle_New failed";
                next = "check center/radius and layer lock state";
                return false;
            }
        } else {
            err  = std::string(where) + ": unknown kind '" + kind + "'";
            next = "supported kinds: box, polygon, wire, circle";
            return false;
        }
        ++drawn;
    }
    result.set("drawn", JVal::N(drawn));
    result.set("layers_created", JVal::N(lc.created));
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    return true;
}

static JVal points_to_json(Ctx& ctx, LObject obj) {
    JVal pts = JVal::A();
    long n = LVertex_GetCount(obj);
    if (n > 0) {
        std::vector<LPoint> raw((size_t)n);
        long got = LVertex_GetArray(obj, &raw[0], (int)n);
        for (long i = 0; i < got; ++i) {
            JVal p = JVal::A();
            p.arr.push_back(JVal::N(i2um(ctx.file, raw[(size_t)i].x)));
            p.arr.push_back(JVal::N(i2um(ctx.file, raw[(size_t)i].y)));
            pts.arr.push_back(p);
        }
    }
    return pts;
}

static JVal rect_to_json(Ctx& ctx, const LRect& r) {
    JVal bb = JVal::A();
    bb.arr.push_back(JVal::N(i2um(ctx.file, r.x0)));
    bb.arr.push_back(JVal::N(i2um(ctx.file, r.y0)));
    bb.arr.push_back(JVal::N(i2um(ctx.file, r.x1)));
    bb.arr.push_back(JVal::N(i2um(ctx.file, r.y1)));
    return bb;
}

static JVal instance_to_json(Ctx& ctx, LInstance inst) {
    JVal e = JVal::O();
    e.set("kind", JVal::S("instance"));
    char nbuf[256] = {0};
    LInstance_GetName(inst, nbuf, sizeof(nbuf) - 1);
    e.set("instance_name", JVal::S(nbuf));
    LCell master = LInstance_GetCell(inst);
    if (master) {
        e.set("cell", JVal::S(cell_name_of(master)));
        e.set("is_tcell", JVal::B(LCell_IsTCell(master) ? true : false));
        JVal cellProps = props_to_json((LEntity)master);
        if (!cellProps.obj.empty()) e.set("cell_properties", cellProps);
    }
    LTransform_Ex99 tf = LInstance_GetTransform_Ex99(inst);
    e.set("x_um", JVal::N(i2um(ctx.file, tf.translation.x)));
    e.set("y_um", JVal::N(i2um(ctx.file, tf.translation.y)));
    e.set("orient", JVal::N(tf.orientation));
    if (tf.magnification.denom)
        e.set("mag", JVal::N((double)tf.magnification.num /
                             (double)tf.magnification.denom));
    LPoint rep = LInstance_GetRepeatCount(inst);
    JVal repA = JVal::A();
    repA.arr.push_back(JVal::N(rep.x));
    repA.arr.push_back(JVal::N(rep.y));
    e.set("repeat", repA);
    LPoint d = LInstance_GetDelta(inst);
    JVal dA = JVal::A();
    dA.arr.push_back(JVal::N(i2um(ctx.file, d.x)));
    dA.arr.push_back(JVal::N(i2um(ctx.file, d.y)));
    e.set("delta_um", dA);
    e.set("bbox_um", rect_to_json(ctx, LInstance_GetMbb(inst)));
    JVal props = props_to_json((LEntity)inst);
    if (!props.obj.empty()) e.set("properties", props);
    return e;
}

// one L-Edit object -> JSON. Returns false (+skippedKind) only when the
// object has no usable geometry at all.
static bool object_to_json(Ctx& ctx, LCell cell, LObject obj, JVal& e,
                           std::string& skippedKind) {
    LShapeType shape = LObject_GetShape(obj);
    if (shape == LObjInstance) {
        LInstance inst = LObject_GetInstance(obj);
        if (!inst) { skippedKind = "instance"; return false; }
        e = instance_to_json(ctx, inst);
        return true;
    }
    LLayer layer = LObject_GetLayer(cell, obj);
    e = JVal::O();
    if (layer) e.set("layer", JVal::S(layer_name_of(layer)));

    if (shape == LBox) {
        e.set("kind", JVal::S("box"));
        e.set("bbox_um", rect_to_json(ctx, LBox_GetRect(obj)));
    } else if (shape == LPolygon) {
        e.set("kind", JVal::S("polygon"));
        e.set("points_um", points_to_json(ctx, obj));
    } else if (shape == LWire) {
        e.set("kind", JVal::S("wire"));
        e.set("points_um", points_to_json(ctx, obj));
        e.set("width_um", JVal::N(i2um(ctx.file, LWire_GetWidth(obj))));
        LCapType cap = LWire_GetCapType(obj);
        e.set("cap", JVal::S(cap == LCapRound ? "round" :
                             cap == LCapExtend ? "extend" : "butt"));
        LJoinType join = LWire_GetJoinType(obj);
        e.set("join", JVal::N((double)join));
    } else if (shape == LCircle) {
        LPoint c = LCircle_GetCenter(obj);
        e.set("kind", JVal::S("circle"));
        JVal cc = JVal::A();
        cc.arr.push_back(JVal::N(i2um(ctx.file, c.x)));
        cc.arr.push_back(JVal::N(i2um(ctx.file, c.y)));
        e.set("center_um", cc);
        e.set("radius_um", JVal::N(i2um(ctx.file, LCircle_GetRadius(obj))));
    } else if (shape == LTorus) {
        LTorusParams tp;
        if (LTorus_GetParams(obj, &tp) == LStatusOK) {
            e.set("kind", JVal::S("torus"));
            JVal cc = JVal::A();
            cc.arr.push_back(JVal::N(i2um(ctx.file, tp.ptCenter.x)));
            cc.arr.push_back(JVal::N(i2um(ctx.file, tp.ptCenter.y)));
            e.set("center_um", cc);
            e.set("inner_radius_um", JVal::N(i2um(ctx.file, tp.nInnerRadius)));
            e.set("outer_radius_um", JVal::N(i2um(ctx.file, tp.nOuterRadius)));
            e.set("start_angle", JVal::N(tp.dStartAngle));
            e.set("stop_angle", JVal::N(tp.dStopAngle));
        } else {
            skippedKind = "torus";
            return false;
        }
    } else if (shape == LPie) {
        LPieParams pp;
        if (LPie_GetParams(obj, &pp) == LStatusOK) {
            e.set("kind", JVal::S("pie"));
            JVal cc = JVal::A();
            cc.arr.push_back(JVal::N(i2um(ctx.file, pp.ptCenter.x)));
            cc.arr.push_back(JVal::N(i2um(ctx.file, pp.ptCenter.y)));
            e.set("center_um", cc);
            e.set("radius_um", JVal::N(i2um(ctx.file, pp.nRadius)));
            e.set("start_angle", JVal::N(pp.dStartAngle));
            e.set("stop_angle", JVal::N(pp.dStopAngle));
        } else {
            skippedKind = "pie";
            return false;
        }
    } else {
        // generic fallback: any exotic kind exposing a vertex outline
        // degrades to a polygon, tagged with its origin
        if (LVertex_GetCount(obj) >= 3) {
            e.set("kind", JVal::S("polygon"));
            e.set("source_kind", JVal::S(shape_kind_name(shape)));
            e.set("points_um", points_to_json(ctx, obj));
        } else {
            skippedKind = shape_kind_name(shape);
            return false;
        }
    }
    // object-level properties (nested via dotted names, e.g. "A.B.C");
    // reported on every converted object, never silently dropped
    JVal oprops = props_to_json((LEntity)obj);
    if (!oprops.obj.empty()) e.set("properties", oprops);
    return true;
}

static bool cmd_get_selection(Ctx& ctx, const JVal&, JVal& result,
                              std::string&, std::string&) {
    JVal objects = JVal::A();
    JVal skipped = JVal::O();   // kind -> count; never silently drop
    int count = 0;
    for (LSelection sel = LSelection_GetList(); sel;
         sel = LSelection_GetNext(sel)) {
        LObject obj = LSelection_GetObject(sel);
        if (!obj) continue;
        ++count;
        JVal e;
        std::string sk;
        if (object_to_json(ctx, ctx.cell, obj, e, sk))
            objects.arr.push_back(e);
        else
            bump(skipped, sk);
    }
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    result.set("count", JVal::N(count));
    result.set("objects", objects);
    result.set("skipped", skipped);
    return true;
}

static bool cmd_get_cell(Ctx& ctx, const JVal&, JVal& result,
                         std::string&, std::string&) {
    JVal objects = JVal::A();
    JVal skipped = JVal::O();
    int count = 0;
    for (LLayer layer = LLayer_GetList(ctx.file); layer;
         layer = LLayer_GetNext(layer)) {
        for (LObject obj = LObject_GetList(ctx.cell, layer); obj;
             obj = LObject_GetNext(obj)) {
            ++count;
            JVal e;
            std::string sk;
            if (object_to_json(ctx, ctx.cell, obj, e, sk))
                objects.arr.push_back(e);
            else
                bump(skipped, sk);
        }
    }
    for (LInstance inst = LInstance_GetList(ctx.cell); inst;
         inst = LInstance_GetNext(inst)) {
        ++count;
        objects.arr.push_back(instance_to_json(ctx, inst));
    }
    // ports and labels are first-class content, enumerated separately
    JVal ports = JVal::A();
    for (LPort port = LPort_GetList(ctx.cell); port;
         port = LPort_GetNext(port)) {
        JVal e = JVal::O();
        char text[512] = {0};
        LPort_GetText(port, text, sizeof(text) - 1);
        e.set("text", JVal::S(text));
        e.set("rect_um", rect_to_json(ctx, LPort_GetRect(port)));
        ports.arr.push_back(e);
    }
    result.set("ports", ports);
    JVal labels = JVal::A();
    for (LLabel label = LLabel_GetList(ctx.cell); label;
         label = LLabel_GetNext(label)) {
        JVal e = JVal::O();
        char text[512] = {0};
        LLabel_GetName(label, text, sizeof(text) - 1);
        e.set("text", JVal::S(text));
        LLayer llayer = LLabel_GetLayer(label);
        if (llayer) e.set("layer", JVal::S(layer_name_of(llayer)));
        LPoint pos = LLabel_GetPosition(label);
        JVal pp = JVal::A();
        pp.arr.push_back(JVal::N(i2um(ctx.file, pos.x)));
        pp.arr.push_back(JVal::N(i2um(ctx.file, pos.y)));
        e.set("position_um", pp);
        e.set("text_size_um", JVal::N(i2um(ctx.file,
                                           LLabel_GetTextSize(label))));
        labels.arr.push_back(e);
    }
    result.set("labels", labels);
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    result.set("is_tcell", JVal::B(LCell_IsTCell(ctx.cell) ? true : false));
    JVal cellProps = props_to_json((LEntity)ctx.cell);
    if (!cellProps.obj.empty()) result.set("properties", cellProps);
    result.set("count", JVal::N(count));
    result.set("objects", objects);
    result.set("skipped", skipped);
    return true;
}

static bool cmd_list_cells(Ctx& ctx, const JVal&, JVal& result,
                           std::string&, std::string&) {
    JVal cells = JVal::A();
    for (LCell cell = LCell_GetList(ctx.file); cell;
         cell = LCell_GetNext(cell)) {
        JVal e = JVal::O();
        e.set("name", JVal::S(cell_name_of(cell)));
        e.set("is_tcell", JVal::B(LCell_IsTCell(cell) ? true : false));
        // auto-generated T-Cell variants carry System.Hide In Lists
        long hidden = 0;
        if (LEntity_PropertyExists((LEntity)cell, "System.Hide In Lists")
                == LStatusOK &&
            LEntity_GetPropertyValue((LEntity)cell, "System.Hide In Lists",
                                     &hidden, sizeof(hidden)) == LStatusOK &&
            hidden)
            e.set("hidden", JVal::B(true));
        cells.arr.push_back(e);
    }
    result.set("cells", cells);
    return true;
}

// programmatic T-Cell instancing with parameters (LInstance_GenerateV);
// returns the generated variant cell name so the caller can harvest it
static bool cmd_instance_tcell(Ctx& ctx, const JVal& params, JVal& result,
                               std::string& err, std::string& next) {
    std::string tcellName = params.str_or("tcell", "");
    if (tcellName.empty()) {
        err  = "params.tcell (T-Cell generator name) is required";
        next = "call list_cells and pick a cell with is_tcell:true";
        return false;
    }
    LCell tcell = LCell_Find(ctx.file, tcellName.c_str());
    if (!tcell) {
        err  = "T-Cell not found: " + tcellName;
        next = "call list_cells for exact names (case-sensitive)";
        return false;
    }
    // params.params -> ["name","value",...,NULL]; all values stringified
    std::vector<std::string> kv;
    const JVal* pp = params.get("params");
    if (pp && pp->t == JVal::JOBJ) {
        for (size_t i = 0; i < pp->obj.size(); ++i) {
            kv.push_back(pp->obj[i].first);
            const JVal& v = pp->obj[i].second;
            if (v.t == JVal::JSTR) kv.push_back(v.str);
            else if (v.t == JVal::JNUM) {
                char buf[64];
                if (v.num == (double)(long long)v.num)
                    sprintf(buf, "%lld", (long long)v.num);
                else
                    sprintf(buf, "%.10g", v.num);
                kv.push_back(buf);
            } else if (v.t == JVal::JBOOL) kv.push_back(v.b ? "1" : "0");
            else {
                err  = "unsupported param value type for: " + pp->obj[i].first;
                next = "pass numbers, strings or booleans";
                return false;
            }
        }
    }
    std::vector<char*> argv;
    for (size_t i = 0; i < kv.size(); ++i)
        argv.push_back(const_cast<char*>(kv[i].c_str()));
    argv.push_back((char*)0);

    LInstance inst = LInstance_GenerateV(ctx.cell, tcell, &argv[0]);
    if (!inst) {
        err  = "LInstance_GenerateV failed for: " + tcellName;
        next = "check parameter names against the T-Cell's DO-NOT-EDIT code "
               "section (get_cell reports System.TCell Code)";
        return false;
    }
    // reposition if requested (generate places at a default location)
    if (params.get("x_um") || params.get("y_um")) {
        LTransform_Ex99 tf = LInstance_GetTransform_Ex99(inst);
        tf.translation = LPoint_Set(
            um2i(ctx.file, params.num_or("x_um", 0.0)),
            um2i(ctx.file, params.num_or("y_um", 0.0)));
        LInstance_Set_Ex99(ctx.cell, inst, tf,
                           LPoint_Set(1, 1), LPoint_Set(0, 0));
    }
    LDisplay_Refresh();
    result = instance_to_json(ctx, inst);
    return true;
}

// EXPERIMENTAL: write T-Cell generator code into a cell's System properties.
// Empirically the code lives in "System.TCell Code" (string) +
// "System.TCell Code Language" (int); whether L-Edit accepts externally
// written code is exactly what this command exists to test.
static bool cmd_set_tcell_code(Ctx& ctx, const JVal& params, JVal& result,
                               std::string& err, std::string& next) {
    std::string cellName = params.str_or("cell", "");
    std::string code = params.str_or("code", "");
    if (cellName.empty() || code.empty()) {
        err  = "params.cell and params.code are required";
        next = "pass the target cell name and the full generator source text";
        return false;
    }
    LCell cell = LCell_Find(ctx.file, cellName.c_str());
    bool created = false;
    if (!cell) {
        cell = LCell_New(ctx.file, cellName.c_str());
        created = true;
    }
    if (!cell) {
        err  = "cannot find or create cell: " + cellName;
        next = "check the cell name";
        return false;
    }
    long language = (long)params.num_or("language", 5);  // 5 = C++ (observed)
    if (LEntity_AssignProperty((LEntity)cell, "System.TCell Code",
                               L_string, code.c_str()) != LStatusOK) {
        err  = "LEntity_AssignProperty failed for System.TCell Code";
        next = "the cell may be locked, or property writing is restricted here";
        return false;
    }
    if (LEntity_AssignProperty((LEntity)cell, "System.TCell Code Language",
                               L_int, &language) != LStatusOK) {
        err  = "failed to set System.TCell Code Language";
        next = "code was written; language tag failed - inspect the cell in L-Edit";
        return false;
    }
    // optional parameter-table definition:
    // params: [{name, type: "bool"|"int"|"float"|"string"|"layer", default}]
    const JVal* defs = params.get("params");
    int nParams = 0;
    if (defs && defs->t == JVal::JARR) {
        for (size_t i = 0; i < defs->arr.size(); ++i) {
            const JVal& d = defs->arr[i];
            std::string pname = d.str_or("name", "");
            std::string ptype = d.str_or("type", "float");
            std::string pdef  = d.str_or("default", "");
            if (d.get("default") && d.get("default")->t == JVal::JNUM) {
                char buf[64];
                double dv = d.get("default")->num;
                if (dv == (double)(long long)dv)
                    sprintf(buf, "%lld", (long long)dv);
                else
                    sprintf(buf, "%.10g", dv);
                pdef = buf;
            }
            if (pname.empty()) {
                err  = "params[].name is required";
                next = "each parameter needs {name, type, default}";
                return false;
            }
            LTCellParameterType t =
                (ptype == "bool")   ? LTC_bool  :
                (ptype == "int")    ? LTC_int   :
                (ptype == "string") ? LTC_string :
                (ptype == "layer")  ? LTC_layer : LTC_float;
            if (LCell_AddTCellParameter(cell, pname.c_str(), t,
                                        pdef.c_str()) != LStatusOK) {
                err  = "LCell_AddTCellParameter failed for: " + pname;
                next = "parameter may already exist with another type; "
                       "inspect the cell's T-Cell Parameters tab";
                return false;
            }
            ++nParams;
        }
    }
    result.set("params_added", JVal::N(nParams));
    result.set("cell", JVal::S(cell_name_of(cell)));
    result.set("created", JVal::B(created));
    result.set("is_tcell", JVal::B(LCell_IsTCell(cell) ? true : false));
    result.set("code_bytes", JVal::N((double)code.size()));
    return true;
}

// T-Cell parameter defaults/previous values for caller-supplied names
// (names come from parsing the DO-NOT-EDIT code section client-side)
static bool cmd_get_tcell_params(Ctx& ctx, const JVal& params, JVal& result,
                                 std::string& err, std::string& next) {
    const JVal* names = params.get("names");
    if (!names || names->t != JVal::JARR || names->arr.empty()) {
        err  = "params.names must be a non-empty array of parameter names";
        next = "parse them from System.TCell Code (get_cell) and pass here";
        return false;
    }
    if (!LCell_IsTCell(ctx.cell)) {
        err  = "cell is not a T-Cell: " + cell_name_of(ctx.cell);
        next = "call list_cells and pick a cell with is_tcell:true";
        return false;
    }
    JVal out = JVal::O();
    for (size_t i = 0; i < names->arr.size(); ++i) {
        if (names->arr[i].t != JVal::JSTR) continue;
        const std::string& pname = names->arr[i].str;
        JVal entry = JVal::O();
        char buf[512] = {0};
        if (LCell_GetTCellDefaultValue(ctx.cell, pname.c_str(), buf,
                                       sizeof(buf) - 1) == LStatusOK)
            entry.set("default", JVal::S(buf));
        memset(buf, 0, sizeof(buf));
        if (LCell_GetTCellPreviousValue(ctx.cell, pname.c_str(), buf,
                                        sizeof(buf) - 1) == LStatusOK)
            entry.set("previous", JVal::S(buf));
        out.set(pname, entry);
    }
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    result.set("params", out);
    return true;
}

// full design-rule table: the process knowledge the tdb carries
static bool cmd_get_drc_rules(Ctx& ctx, const JVal&, JVal& result,
                              std::string&, std::string&) {
    static const char* kRuleNames[] = {
        "MIN_WIDTH", "EXACT_WIDTH", "OVERLAP", "EXTENSION",
        "NOT_EXISTS", "SPACING", "SURROUND", "DENSITY",
    };
    JVal rules = JVal::A();
    for (LDrcRule rule = LDrcRule_GetList(ctx.file); rule;
         rule = LDrcRule_GetNext(rule)) {
        LDesignRuleParam rp;
        memset(&rp, 0, sizeof(rp));
        if (!LDrcRule_GetParameters(rule, &rp)) continue;
        JVal e = JVal::O();
        if (rp.name) e.set("name", JVal::S(rp.name));
        int t = (int)rp.rule_type;
        e.set("type", JVal::S((t >= 0 && t < 8) ? kRuleNames[t] : "?"));
        if (rp.layer1) e.set("layer1", JVal::S(rp.layer1));
        if (rp.layer2) e.set("layer2", JVal::S(rp.layer2));
        e.set("enabled", JVal::B(rp.enable != 0));
        if (rp.use_internal_units)
            e.set("distance_um", JVal::N(i2um(ctx.file, (LCoord)rp.distance)));
        else
            e.set("distance_raw", JVal::N((double)rp.distance));
        rules.arr.push_back(e);
        LDrcRule_DestroyParameter(&rp);
    }
    result.set("rules", rules);
    return true;
}

// Make `file` the visible/active design and verify it took. Every command
// resolves its target via LFile_GetVisible(), so a created-but-not-activated
// design silently redirects all subsequent writes into the previously active
// design (v0.5.0 bug, caught by blind test: a fresh LFile_New has no open
// window, so the user's design stayed active and a demo drew into it).
static bool activate_design(LFile file, JVal& result,
                            std::string& err, std::string& next) {
    LCell first = LCell_GetList(file);
    if (!first) first = LCell_New(file, "Cell0");
    if (first) {
        std::string cname = cell_name_of(first);
        LFile_OpenCell(file, cname.c_str());
        LCell_MakeVisible(first);
        result.set("cell", JVal::S(cname));
    }
    LDisplay_Refresh();
    if (LFile_GetVisible() != file) {
        err  = "design '" + file_name_of(file) + "' was created/opened but "
               "could not be made the active design; the previous design is "
               "still active and would receive all writes";
        next = "switch to it manually in L-Edit (Window menu), then ping and "
               "confirm 'file' changed before drawing";
        return false;
    }
    result.set("file", JVal::S(file_name_of(file)));
    return true;
}

static bool cmd_new_design(Ctx&, const JVal& params, JVal& result,
                           std::string& err, std::string& next) {
    std::string name = params.str_or("name", "klink_design");
    LFile setup = 0;
    if (params.bool_or("setup_from_visible", false)) {
        setup = LFile_GetVisible();
        if (!setup) {
            err  = "setup_from_visible requested but no design is open";
            next = "omit setup_from_visible, or open the template design first";
            return false;
        }
    }
    // The most common reason LFile_New fails is a design of that name being
    // ALREADY OPEN -- say so, instead of a bare "failed" the caller cannot act on.
    if (LFile_Find(name.c_str())) {
        err  = "a design named '" + name + "' is already open";
        next = "call activate_design {file:\"" + name + "\"} to switch to it, "
               "or pick a different name";
        return false;
    }
    LFile nf = LFile_New(setup, name.c_str());
    if (!nf) {
        err  = "LFile_New failed for: " + name;
        next = "check the name (no path separators, not already open) "
               "and try again";
        return false;
    }
    return activate_design(nf, result, err, next);
}

static bool cmd_open_design(Ctx&, const JVal& params, JVal& result,
                            std::string& err, std::string& next) {
    std::string path = params.str_or("path", "");
    if (path.empty()) {
        err  = "params.path is required";
        next = "pass the absolute path of a .tdb file";
        return false;
    }
    // Pre-check existence ourselves: a failed LFile_Open pops a modal error
    // dialog, and a modal dialog freezes the bridge timer (heartbeat stall).
    FILE* probe = fopen(path.c_str(), "rb");
    if (!probe && path.size() >= 4 &&
        _stricmp(path.c_str() + path.size() - 4, ".tdb") != 0) {
        std::string withExt = path + ".tdb";
        probe = fopen(withExt.c_str(), "rb");
        if (probe) path = withExt;
    }
    if (!probe) {
        err  = "design file not found: " + path;
        next = "pass the absolute path of an existing .tdb "
               "(new_design creates a fresh one; GDS import is a separate flow)";
        return false;
    }
    fclose(probe);
    LFile f = LFile_Open(path.c_str(), LTdbFile);
    if (!f) {
        err  = "LFile_Open failed for: " + path;
        next = "check the file is a valid .tdb (GDS import is a separate flow)";
        return false;
    }
    return activate_design(f, result, err, next);
}

static bool cmd_save_design(Ctx& ctx, const JVal& params, JVal& result,
                            std::string& err, std::string& next) {
    std::string path = params.str_or("path", "");
    LStatus st = path.empty() ? LFile_Save(ctx.file)
                              : LFile_SaveAs(ctx.file, path.c_str(), LTdbFile);
    if (st != LStatusOK) {
        err  = "save failed";
        next = path.empty()
            ? "the design may never have been saved; pass params.path for save-as"
            : "check the target path is writable";
        return false;
    }
    result.set("file", JVal::S(file_name_of(ctx.file)));
    result.set("saved_as", JVal::S(path));
    return true;
}

// Every command targets LFile_GetVisible(), so a caller that cannot SEE the
// other open designs cannot reason about which one it is writing to. Until
// now `ping` reported only the visible one.
static bool cmd_list_designs(Ctx&, const JVal&, JVal& result,
                             std::string&, std::string&) {
    LFile visible = LFile_GetVisible();
    JVal arr = JVal::A();
    int n = 0;
    for (LFile f = LFile_GetList(); f; f = LFile_GetNext(f)) {
        JVal e = JVal::O();
        e.set("file", JVal::S(file_name_of(f)));
        e.set("visible", JVal::B(f == visible));
        e.set("changed", JVal::B(LFile_IsChanged(f) != 0));
        int cells = 0;
        for (LCell c = LCell_GetList(f); c; c = LCell_GetNext(c)) ++cells;
        e.set("cells", JVal::N((double)cells));
        arr.arr.push_back(e);
        ++n;
    }
    result.set("designs", arr);
    result.set("count", JVal::N((double)n));
    return true;
}

// Switch between designs that are ALREADY OPEN, by name -- no disk path
// needed, so a design created by new_design and never saved is reachable
// too. open_design {path} remains the way to load one from disk.
static bool cmd_activate_design(Ctx&, const JVal& params, JVal& result,
                                std::string& err, std::string& next) {
    std::string name = params.str_or("file", "");
    if (name.empty()) name = params.str_or("name", "");
    if (name.empty()) {
        err  = "params.file is required";
        next = "call list_designs and pass one of the 'file' values it reports";
        return false;
    }
    LFile target = 0;
    for (LFile f = LFile_GetList(); f; f = LFile_GetNext(f)) {
        if (file_matches(file_name_of(f), name)) { target = f; break; }
    }
    if (!target) {
        err  = "no OPEN design matches: " + name;
        next = "call list_designs to see what is open; use open_design {path} "
               "to load one from disk first";
        return false;
    }
    if (LFile_GetVisible() == target) {
        result.set("file", JVal::S(file_name_of(target)));
        result.set("switched", JVal::B(false));
        return true;
    }
    // Prefer raising an EXISTING window of that design: unlike the
    // create/open path this touches nothing inside the design itself (no
    // Cell0 conjured into an empty library just to make it visible).
    for (LWindow w = LWindow_GetList(); w; w = LWindow_GetNext(w)) {
        if (LWindow_GetFile(w) != target) continue;
        LWindow_MakeVisible(w);
        if (LFile_GetVisible() != target) continue;
        LCell vc = LCell_GetVisible();
        result.set("file", JVal::S(file_name_of(target)));
        result.set("cell", JVal::S(vc ? cell_name_of(vc) : ""));
        result.set("switched", JVal::B(true));
        result.set("via", JVal::S("window"));
        return true;
    }
    // No window open on it: fall back to opening one of its own cells.
    LCell first = LCell_GetList(target);
    if (first) {
        std::string cname = cell_name_of(first);
        LFile_OpenCell(target, cname.c_str());
        LCell_MakeVisible(first);
        LDisplay_Refresh();
        if (LFile_GetVisible() == target) {
            result.set("file", JVal::S(file_name_of(target)));
            result.set("cell", JVal::S(cname));
            result.set("switched", JVal::B(true));
            result.set("via", JVal::S("cell"));
            return true;
        }
    }
    err  = "could not make '" + name + "' the active design";
    next = "switch to it manually in L-Edit (Window menu), then ping and "
           "confirm 'file' before writing";
    return false;
}

// Bulk transfer that the JSON transport is the wrong tool for: GDS carries
// the whole CELL HIERARCHY (no flattening), ignores the 64 KiB request cap,
// and overwrite=all makes a re-import idempotent where `draw` only appends.
// Small/incremental/parametric work still belongs on the RPC path -- this
// does not replace it.
static bool cmd_import_gds(Ctx& ctx, const JVal& params, JVal& result,
                           std::string& err, std::string& next) {
    std::string path = params.str_or("path", "");
    if (path.empty()) {
        err  = "params.path is required";
        next = "pass the absolute path of a .gds/.gds2 file to import";
        return false;
    }
    FILE* probe = fopen(path.c_str(), "rb");
    if (!probe) {
        err  = "GDS file not found: " + path;
        next = "pass an absolute path to an existing file (write it from "
               "KLayout first)";
        return false;
    }
    fclose(probe);

    std::string mode = params.str_or("overwrite", "all");
    LOverwriteCellsScopeOnImport scope = cOverwriteAllCells;
    if (mode == "none")     scope = cDontOverwriteCells;
    else if (mode == "top") scope = cOverwriteCellsInTopDesignOnly;
    else if (mode != "all") {
        err  = "unknown overwrite mode: " + mode;
        next = "use \"all\" (idempotent re-import), \"top\" or \"none\"";
        return false;
    }

    std::string logPath = params.str_or("log_path", "");
    if (logPath.empty()) logPath = g_root + "\\import_gds.log";

    int before = 0;
    for (LCell c = LCell_GetList(ctx.file); c; c = LCell_GetNext(c)) ++before;

    LStatus st = LFile_ImportGDSII(
        ctx.file, path.c_str(),
        params.bool_or("use_gds_datatype", true) ? LTRUE : LFALSE,
        scope,
        LTRUE,          // take the resolution from the GDS file itself
        0.0,
        logPath.c_str());
    if (st != LStatusOK) {
        char buf[96];
        _snprintf(buf, sizeof(buf) - 1, "LFile_ImportGDSII failed (status %d)",
                  (int)st);
        buf[sizeof(buf) - 1] = 0;
        err  = buf;
        next = "read the import log at " + logPath;
        return false;
    }

    // LStatusOK is NOT proof of success: an aborted import still returns it,
    // leaving EMPTY cell shells behind (proven with a KLayout-written file
    // that stopped at "Unexpected element ... Import Aborted"). The log is
    // the real verdict, so read it before claiming the geometry arrived.
    std::string log;
    if (read_file(logPath, log)) {
        bool aborted = log.find("Import Aborted") != std::string::npos;
        bool errors  = log.find("error(s)") != std::string::npos &&
                       log.find("0 error(s)") == std::string::npos;
        if (aborted || errors) {
            // Lead with the diagnosis, not with whatever character a fixed
            // tail happened to land on (it used to start mid-path).
            size_t at = log.find("Error #");
            if (at == std::string::npos) at = log.find("Warning");
            std::string cut = (at != std::string::npos)
                ? log.substr(at)
                : (log.size() > 400 ? log.substr(log.size() - 400) : log);
            if (cut.size() > 400) cut.erase(400);
            err  = "GDS import reported errors: " + cut;
            next = "L-Edit's reader is strict about the trailing 2048-byte "
                   "block: a file written by another tool may need zero "
                   "padding to a multiple of 2048 bytes (the klink client's "
                   "import_gds() does this for you). Full log: " + logPath;
            return false;
        }
    }

    int after = 0;
    for (LCell c = LCell_GetList(ctx.file); c; c = LCell_GetNext(c)) ++after;
    LDisplay_Refresh();
    result.set("cells_before", JVal::N((double)before));
    result.set("cells_after", JVal::N((double)after));
    result.set("cells_added", JVal::N((double)(after - before)));
    result.set("log_path", JVal::S(logPath));
    return true;
}

static bool cmd_clear_cell(Ctx& ctx, const JVal& params, JVal& result,
                           std::string& err, std::string& next) {
    if (params.str_or("cell", "").empty()) {
        err  = "clear_cell requires an explicit params.cell (destructive op)";
        next = "pass {\"cell\":\"<name>\"}; the visible cell is never cleared implicitly";
        return false;
    }
    int nShapes = 0, nInst = 0;
    for (LLayer layer = LLayer_GetList(ctx.file); layer;
         layer = LLayer_GetNext(layer)) {
        for (LObject obj = LObject_GetList(ctx.cell, layer); obj; ) {
            LObject nextObj = LObject_GetNext(obj);
            if (LObject_Delete(ctx.cell, obj) == LStatusOK) ++nShapes;
            obj = nextObj;
        }
    }
    for (LInstance inst = LInstance_GetList(ctx.cell); inst; ) {
        LInstance nextInst = LInstance_GetNext(inst);
        if (LInstance_Delete(ctx.cell, inst) == LStatusOK) ++nInst;
        inst = nextInst;
    }
    LDisplay_Refresh();
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    result.set("deleted_shapes", JVal::N(nShapes));
    result.set("deleted_instances", JVal::N(nInst));
    return true;
}

static bool cmd_place_instance(Ctx& ctx, const JVal& params, JVal& result,
                               std::string& err, std::string& next) {
    std::string childName = params.str_or("child", "");
    if (childName.empty()) {
        err  = "params.child (cell name to instance) is required";
        next = "create/draw the child cell first, then place it";
        return false;
    }
    LCell child = LCell_Find(ctx.file, childName.c_str());
    if (!child) {
        err  = "child cell not found: " + childName;
        next = "call create_cell + draw first; names are case-sensitive";
        return false;
    }
    double x = params.num_or("x_um", 0.0), y = params.num_or("y_um", 0.0);
    int    orient  = (int)params.num_or("orient", 0);
    bool   mirrorX = params.bool_or("mirror_x", false);
    int    nx = (int)params.num_or("nx", 1), ny = (int)params.num_or("ny", 1);
    double dx = params.num_or("dx_um", 0.0), dy = params.num_or("dy_um", 0.0);
    if (nx < 1 || ny < 1) {
        err  = "nx/ny must be >= 1";
        next = "fix the array counts and resend";
        return false;
    }
    if ((nx > 1 && dx <= 0.0) || (ny > 1 && dy <= 0.0)) {
        err  = "array placement needs dx_um/dy_um > 0 for nx/ny > 1";
        next = "pass the array pitch in microns";
        return false;
    }
    // orientation encoding per ldata.h defines:
    //   plain: 0/90/180/270; mirrored-X: 0->-360, 90->-90, 180->-180, 270->-270
    float o;
    if (!mirrorX) o = (float)orient;
    else          o = (orient == 0) ? -360.0f : (float)(-orient);

    LTransform_Ex99 tf;
    tf.translation = LPoint_Set(um2i(ctx.file, x), um2i(ctx.file, y));
    tf.orientation = o;
    tf.magnification.num = 1;
    tf.magnification.denom = 1;

    LInstance inst = LInstance_New_Ex99(
        ctx.cell, child, tf,
        LPoint_Set(nx, ny),
        LPoint_Set(um2i(ctx.file, dx), um2i(ctx.file, dy)));
    if (!inst) {
        err  = "LInstance_New_Ex99 failed";
        next = "check the child is not an ancestor of the target cell (no recursive instancing)";
        return false;
    }
    result.set("placed", JVal::B(true));
    result.set("cell", JVal::S(cell_name_of(ctx.cell)));
    return true;
}

// nearest palette entry to an RGB request (L-Edit colors are palette-indexed;
// we never rewrite the palette itself — other layers may share entries)
static int nearest_palette_index(LFile file, int r, int g, int b,
                                 LColor* matched) {
    int n = LFile_GetColorPaletteNumColors(file);
    int best = 0;
    long bestd = 0x7FFFFFFF;
    for (int i = 0; i < n; ++i) {
        LColor c;
        if (LFile_GetColorPalette(file, &c, i) != LStatusOK) continue;
        long dr = (long)c.LRed - r, dg = (long)c.LGreen - g,
             db = (long)c.LBlue - b;
        long d = dr * dr + dg * dg + db * db;
        if (d < bestd) {
            bestd = d;
            best = i;
            if (matched) *matched = c;
        }
    }
    return best;
}

static bool read_rgb(const JVal* v, int rgb[3]) {
    if (!v || v->t != JVal::JARR || v->arr.size() != 3) return false;
    for (int i = 0; i < 3; ++i) {
        if (v->arr[(size_t)i].t != JVal::JNUM) return false;
        rgb[i] = (int)v->arr[(size_t)i].num;
        if (rgb[i] < 0) rgb[i] = 0;
        if (rgb[i] > 255) rgb[i] = 255;
    }
    return true;
}

static bool cmd_set_layer_style(Ctx& ctx, const JVal& params, JVal& result,
                                std::string& err, std::string& next) {
    std::string name = params.str_or("layer", "");
    if (name.empty()) {
        err  = "params.layer is required";
        next = "pass {\"layer\":\"WL\", \"fill_rgb\":[0,90,255]}";
        return false;
    }
    LLayer layer = LLayer_Find(ctx.file, name.c_str());
    if (!layer) {
        err  = "layer not found: " + name;
        next = "call ensure_layer first; see get_layers for the current table";
        return false;
    }
    LRenderingAttribute ra;
    memset(&ra, 0, sizeof(ra));
    if (LLayer_GetRenderingAttribute(layer, raiObject, &ra) != LStatusOK) {
        err  = "LLayer_GetRenderingAttribute failed for: " + name;
        next = "this layer may be a special/system layer; style a mask layer instead";
        return false;
    }

    int rgb[3];
    if (read_rgb(params.get("fill_rgb"), rgb)) {
        LColor got;
        memset(&got, 0, sizeof(got));
        ra.mFillColorIndex =
            (unsigned int)nearest_palette_index(ctx.file, rgb[0], rgb[1],
                                                rgb[2], &got);
        // fill_rgb implies you want to SEE the fill -- but NOT solid:
        // solid hides the layer underneath, and reading a layout means
        // reading the stack (a contact landing on its metal). Hatch by
        // default, name-hashed so layers differ; pass fill:"solid" to
        // override. Outline defaults to NONE (owner ruling: borderless
        // fills make the selection highlight legible).
        memcpy(ra.mFillPattern, AUTO_STIPPLES[hash_name(name) % 8],
               sizeof(LStipple));
        if (!params.get("outline_rgb"))
            memset(ra.mOutlinePattern, 0x00, sizeof(LStipple));
        JVal used = JVal::A();
        used.arr.push_back(JVal::N(got.LRed));
        used.arr.push_back(JVal::N(got.LGreen));
        used.arr.push_back(JVal::N(got.LBlue));
        result.set("fill_rgb_used", used);
        result.set("fill_color_index", JVal::N(ra.mFillColorIndex));
    }
    std::string fill = params.str_or("fill", "");
    if (fill == "none")       memset(ra.mFillPattern, 0x00, sizeof(LStipple));
    else if (fill == "solid") memset(ra.mFillPattern, 0xFF, sizeof(LStipple));
    else if (fill == "hatch")
        memcpy(ra.mFillPattern, AUTO_STIPPLES[hash_name(name) % 8],
               sizeof(LStipple));
    else if (!fill.empty()) {
        err  = "unknown fill mode: " + fill;
        next = "supported: \"hatch\" (default, lets you see the stack), "
               "\"solid\", \"none\"";
        return false;
    }
    if (read_rgb(params.get("outline_rgb"), rgb)) {
        LColor got;
        memset(&got, 0, sizeof(got));
        ra.mOutlineColorIndex =
            (unsigned int)nearest_palette_index(ctx.file, rgb[0], rgb[1],
                                                rgb[2], &got);
        memset(ra.mOutlinePattern, 0xFF, sizeof(LStipple));
        result.set("outline_color_index", JVal::N(ra.mOutlineColorIndex));
    }

    if (LLayer_SetRenderingAttribute(layer, raiObject, &ra) != LStatusOK) {
        err  = "LLayer_SetRenderingAttribute failed for: " + name;
        next = "check the layer is not locked/protected";
        return false;
    }
    LDisplay_Refresh();
    result.set("layer", JVal::S(name));
    return true;
}

struct Command {
    const char* name;
    CmdHandler  handler;
    bool        needs_file;
    bool        needs_cell;
};
// Forward declaration: defined right after the table it walks.
static bool dispatch_one(const std::string& cmd, const JVal& params,
                         JVal& result, std::string& err, std::string& next,
                         bool* known);

// ONE request, N ordered commands.
//
// The transport costs about one poll interval PER REQUEST no matter how big
// the payload is (measured: 400 boxes drew in 0.16 s, of which ~0.15 s was
// waiting for the tick). So a hierarchy push -- create_cell -> draw ->
// place_instance, repeated per cell -- is dominated by round trips, not by
// L-Edit. Batch them and the round trips collapse to one.
//
// Ops execute IN ORDER and stop at the first failure, so later ops may
// depend on earlier ones (unlike pipelining independent requests, where the
// tick's file order decides). Each op resolves its own target cell/design.
static bool cmd_batch(Ctx&, const JVal& params, JVal& result,
                      std::string& err, std::string& next) {
    const JVal* ops = params.get("ops");
    if (!ops || ops->t != JVal::JARR || ops->arr.empty()) {
        err  = "params.ops must be a non-empty array";
        next = "pass ops:[{\"cmd\":\"create_cell\",\"params\":{\"name\":\"X\"}},"
               "{\"cmd\":\"draw\",\"params\":{\"cell\":\"X\",\"items\":[...]}}]";
        return false;
    }
    JVal results = JVal::A();
    JVal empty = JVal::O();
    for (size_t i = 0; i < ops->arr.size(); ++i) {
        const JVal& op = ops->arr[i];
        std::string oc = op.str_or("cmd", "");
        std::string oerr, onext;
        JVal one = JVal::O();
        bool known = false, ok = false;
        if (oc == "batch") {
            oerr  = "batch cannot contain another batch";
            onext = "flatten the ops list";
        } else {
            const JVal* p = op.get("params");
            ok = dispatch_one(oc, (p && p->t == JVal::JOBJ) ? *p : empty,
                              one, oerr, onext, &known);
            if (!known) {
                oerr  = "unknown cmd: " + oc;
                onext = "call ping for the capability list";
            }
        }
        if (!ok) {
            char where[48];
            sprintf(where, "ops[%d]: ", (int)i);
            err  = std::string(where) + oerr;
            next = onext;
            result.set("results", results);
            result.set("completed", JVal::N((double)i));
            return false;
        }
        results.arr.push_back(one);
        // A long batch runs inside ONE tick, and the tick is what refreshes
        // hello.json -- without this the bridge would look DEAD (stale
        // heartbeat) while it is merely busy, which is the single most
        // misleading failure the bridge can present.
        DWORD now = GetTickCount();
        if (now - g_lastHelloMs >= HELLO_EVERY_MS) {
            write_hello();
            g_lastHelloMs = now;
        }
    }
    result.set("results", results);
    result.set("completed", JVal::N((double)ops->arr.size()));
    return true;
}

static const Command g_commands[] = {
    // name             handler            needs_file  needs_cell
    { "ping",            cmd_ping,            false, false },
    { "batch",           cmd_batch,           false, false },
    { "get_layers",      cmd_get_layers,      true,  false },
    { "ensure_layer",    cmd_ensure_layer,    true,  false },
    { "create_cell",     cmd_create_cell,     true,  false },
    { "draw",            cmd_draw,            true,  true  },
    { "get_selection",   cmd_get_selection,   true,  true  },
    { "place_instance",  cmd_place_instance,  true,  true  },
    { "set_layer_style", cmd_set_layer_style, true,  false },
    { "get_cell",        cmd_get_cell,        true,  true  },
    { "clear_cell",      cmd_clear_cell,      true,  true  },
    { "list_cells",      cmd_list_cells,      true,  false },
    { "instance_tcell",  cmd_instance_tcell,  true,  true  },
    { "set_tcell_code",  cmd_set_tcell_code,  true,  false },
    { "new_design",      cmd_new_design,      false, false },
    { "open_design",     cmd_open_design,     false, false },
    { "save_design",     cmd_save_design,     true,  false },
    { "list_designs",    cmd_list_designs,    false, false },
    { "activate_design", cmd_activate_design, false, false },
    { "import_gds",      cmd_import_gds,      true,  false },
    { "get_tcell_params", cmd_get_tcell_params, true, true  },
    { "get_drc_rules",   cmd_get_drc_rules,   true,  false },
};

// Resolve + run one command. Shared by the top-level request handler and by
// `batch`, so a batched op behaves exactly like a standalone one: its own
// Ctx (target design/cell), its own expect_file guard, its own file echo.
static bool dispatch_one(const std::string& cmd, const JVal& params,
                         JVal& result, std::string& err, std::string& next,
                         bool* known) {
    if (known) *known = false;
    for (size_t i = 0; i < sizeof(g_commands) / sizeof(g_commands[0]); ++i) {
        if (cmd != g_commands[i].name) continue;
        if (known) *known = true;
        Ctx ctx;
        bool ok = resolve_ctx(params, ctx, g_commands[i].needs_file,
                              g_commands[i].needs_cell, err, next) &&
                  g_commands[i].handler(ctx, params, result, err, next);
        // Echo the design every file-bound command actually wrote to / read
        // from, so callers can detect targeting drift.
        if (ok && g_commands[i].needs_file && ctx.file && !result.get("file"))
            result.set("file", JVal::S(file_name_of(ctx.file)));
        return ok;
    }
    return false;
}

// ----------------------------------------------------------------------------
// Request processing + polling loop
// ----------------------------------------------------------------------------

static void write_response(const std::string& id, bool ok,
                           const JVal& payload,
                           const std::string& err, const std::string& next) {
    JVal resp = JVal::O();
    resp.set("schema", JVal::N(1));
    resp.set("id", JVal::S(id));
    resp.set("ok", JVal::B(ok));
    if (ok) {
        resp.set("result", payload);
    } else {
        JVal e = JVal::O();
        e.set("code", JVal::S("ERR_BRIDGE"));
        e.set("message", JVal::S(err));
        e.set("next_action", JVal::S(next));
        resp.set("error", e);
    }
    std::string body;
    jdump(resp, body);
    write_file_atomic(g_outbox + "\\resp_" + id + ".json", body);
}

static void process_request_file(const std::string& fname) {
    std::string path = g_inbox + "\\" + fname;

    // An oversized request used to fall through read_file's cap check and
    // return BEFORE the DeleteFileA below: never executed, never answered,
    // never removed -- the caller stalled for its whole timeout while the
    // file was re-read on every tick. Consume it and say why instead.
    long sz = file_size(path);
    if (sz > REQ_CAP_BYTES) {
        std::string id;
        if (!sniff_request_id(path, id)) {
            id = fname;
            size_t d = id.rfind(".json");
            if (d != std::string::npos) id.erase(d);
            if (id.compare(0, 4, "req_") == 0) id.erase(0, 4);
        }
        DeleteFileA(path.c_str());
        char msg[192];
        _snprintf(msg, sizeof(msg) - 1,
                  "request is %ld bytes, over the %d byte cap",
                  sz, (int)REQ_CAP_BYTES);
        msg[sizeof(msg) - 1] = 0;
        write_response(id, false, JVal(), msg,
                       "send it as several smaller requests (the klink "
                       "client's draw() chunks itself), group them with the "
                       "'batch' command, or move bulk geometry as a GDS file "
                       "via import_gds");
        bridge_log(msg);
        return;
    }

    std::string body;
    if (!read_file(path, body)) return;   // partially written? next tick retries
    DeleteFileA(path.c_str());            // consume before handling

    // derive a fallback id from the file name: req_<id>.json -> whole stem
    std::string fallbackId = fname;
    size_t dot = fallbackId.rfind(".json");
    if (dot != std::string::npos) fallbackId.erase(dot);

    JVal req;
    JParser parser(body);
    if (!parser.parse(req) || req.t != JVal::JOBJ) {
        write_response(fallbackId, false, JVal(),
                       "request is not valid JSON",
                       "resend the request as a single UTF-8 JSON object");
        return;
    }
    std::string id  = req.str_or("id", fallbackId.c_str());
    std::string cmd = req.str_or("cmd", "");
    if (g_processed.count(id)) return;    // duplicate: v0.1 drops silently
    append_processed(id);

    char logbuf[512];
    _snprintf(logbuf, sizeof(logbuf) - 1, "request id=%s cmd=%s",
              id.c_str(), cmd.c_str());
    logbuf[sizeof(logbuf) - 1] = 0;
    bridge_log(logbuf);

    const JVal* paramsPtr = req.get("params");
    JVal empty = JVal::O();
    const JVal& params = (paramsPtr && paramsPtr->t == JVal::JOBJ)
                         ? *paramsPtr : empty;

    {
        std::string err, next;
        JVal result = JVal::O();
        bool isKnown = false;
        bool ok = dispatch_one(cmd, params, result, err, next, &isKnown);
        if (isKnown) {
            write_response(id, ok, result, err, next);
            return;
        }
    }
    std::string known;
    for (size_t i = 0; i < sizeof(g_commands) / sizeof(g_commands[0]); ++i) {
        if (i) known += ", ";
        known += g_commands[i].name;
    }
    write_response(id, false, JVal(),
                   "unknown cmd: " + cmd,
                   "supported commands: " + known);
}

static void write_hello() {
    JVal hello = JVal::O();
    hello.set("schema", JVal::N(1));
    hello.set("proto", JVal::N(BRIDGE_PROTO));
    hello.set("macro_version", JVal::S(BRIDGE_MACRO_VERSION));
    hello.set("pid", JVal::N((double)GetCurrentProcessId()));
    hello.set("tick", JVal::N((double)g_tick));
    hello.set("tick_ms", JVal::N((double)GetTickCount()));
    LFile file = LFile_GetVisible();
    hello.set("file", JVal::S(file ? file_name_of(file) : ""));
    LCell cell = LCell_GetVisible();
    hello.set("cell", JVal::S(cell ? cell_name_of(cell) : ""));
    std::string body;
    jdump(hello, body);
    write_file_atomic(g_root + "\\hello.json", body);
}

// Responses whose caller timed out or was killed are never collected --
// four-day-old ones were found sitting in a real exchange dir. Reap them,
// but only well past any plausible caller timeout so a live wait is never
// robbed of its answer.
static void sweep_outbox() {
    WIN32_FIND_DATAA fd;
    HANDLE h = FindFirstFileA((g_outbox + "\\resp_*.json").c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) return;
    FILETIME nowFt;
    GetSystemTimeAsFileTime(&nowFt);
    ULARGE_INTEGER now;
    now.LowPart  = nowFt.dwLowDateTime;
    now.HighPart = nowFt.dwHighDateTime;
    const ULONGLONG maxAge = 15ULL * 60ULL * 10000000ULL;   // 15 min in 100ns
    int reaped = 0;
    do {
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) continue;
        ULARGE_INTEGER w;
        w.LowPart  = fd.ftLastWriteTime.dwLowDateTime;
        w.HighPart = fd.ftLastWriteTime.dwHighDateTime;
        if (now.QuadPart > w.QuadPart &&
            now.QuadPart - w.QuadPart > maxAge) {
            if (DeleteFileA((g_outbox + "\\" + fd.cFileName).c_str())) ++reaped;
        }
    } while (FindNextFileA(h, &fd));
    FindClose(h);
    if (reaped) {
        char buf[96];
        _snprintf(buf, sizeof(buf) - 1,
                  "swept %d uncollected response(s) older than 15 min", reaped);
        buf[sizeof(buf) - 1] = 0;
        bridge_log(buf);
    }
}

static void CALLBACK BridgeTimerProc(HWND, UINT, UINT_PTR, DWORD) {
    if (g_inTick) return;      // a tick arriving mid-request is skipped, not nested
    g_inTick = true;
    ++g_tick;
    // exceptions must never escape into L-Edit's message loop
    try {
        DWORD now = GetTickCount();
        if (now - g_lastHelloMs >= HELLO_EVERY_MS || g_lastHelloMs == 0) {
            write_hello();
            g_lastHelloMs = now;
        }
        if (now - g_lastSweepMs >= SWEEP_EVERY_MS || g_lastSweepMs == 0) {
            sweep_outbox();
            g_lastSweepMs = now;
        }

        // cold bridge: keep the old idle duty cycle instead of scanning the
        // directory 60+ times a second inside someone's EDA tool
        const unsigned skip = POLL_IDLE_MS / POLL_INTERVAL_MS;
        if (g_hotTicks == 0 && skip > 1 && (g_tick % skip) != 0) {
            g_inTick = false;
            return;
        }
        if (g_hotTicks > 0) --g_hotTicks;

        WIN32_FIND_DATAA fd;
        HANDLE h = FindFirstFileA((g_inbox + "\\*.json").c_str(), &fd);
        if (h != INVALID_HANDLE_VALUE) {
            std::vector<std::string> names;
            do {
                if (!(fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY))
                    names.push_back(fd.cFileName);
            } while (FindNextFileA(h, &fd));
            FindClose(h);
            // one tick drains the WHOLE queue, which is what makes client-side
            // pipelining (N requests written up front) ~10x faster than N
            // blocking round trips
            for (size_t i = 0; i < names.size(); ++i)
                process_request_file(names[i]);
            if (!names.empty()) g_hotTicks = POLL_HOT_TICKS;
        }
    } catch (...) {
        bridge_log("EXCEPTION in bridge tick (request skipped)");
    }
    g_inTick = false;
}

// ----------------------------------------------------------------------------
// Menu entry points + UPI entry
// ----------------------------------------------------------------------------

void klink_bridge_start(void) {
    if (g_timerId) {
        bridge_log("bridge already running");
        return;
    }
    if (!init_paths()) {
        LUpi_LogMessage("klink bridge: FAILED to create exchange directories");
        return;
    }
    g_timerId = SetTimer(NULL, 0, POLL_INTERVAL_MS, BridgeTimerProc);
    if (g_timerId) {
        write_hello();
        char buf[512];
        _snprintf(buf, sizeof(buf) - 1, "klink bridge polling %s every %d ms",
                  g_inbox.c_str(), POLL_INTERVAL_MS);
        buf[sizeof(buf) - 1] = 0;
        bridge_log(buf);
    } else {
        bridge_log("ERROR: SetTimer failed, bridge not running");
    }
}

void klink_bridge_stop(void) {
    if (g_timerId) {
        KillTimer(NULL, g_timerId);
        g_timerId = 0;
        bridge_log("klink bridge stopped");
    } else {
        bridge_log("klink bridge was not running");
    }
}

void klink_bridge_status(void) {
    char buf[1024];
    _snprintf(buf, sizeof(buf) - 1,
              "klink bridge: %s | namespace: %s | tick: %u | processed: %u",
              g_timerId ? "RUNNING" : "STOPPED",
              g_root.empty() ? "(uninitialized)" : g_root.c_str(),
              g_tick, (unsigned)g_processed.size());
    buf[sizeof(buf) - 1] = 0;
    bridge_log(buf);
    LDialog_MsgBox(buf);
}

int UPI_Entry_Point(void) {
    LMacro_BindToMenuAndHotKey_v9_30("Tools", NULL, "klink: Bridge Start",
                                     "klink_bridge_start", NULL);
    LMacro_BindToMenuAndHotKey_v9_30("Tools", NULL, "klink: Bridge Stop",
                                     "klink_bridge_stop", NULL);
    LMacro_BindToMenuAndHotKey_v9_30("Tools", NULL, "klink: Bridge Status",
                                     "klink_bridge_status", NULL);
    klink_bridge_start();   // auto-start on load; menu items remain as manual controls
    return 1;
}
