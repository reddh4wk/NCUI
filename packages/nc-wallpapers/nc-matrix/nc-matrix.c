#ifndef _POSIX_C_SOURCE
#define _POSIX_C_SOURCE 200809L
#endif
#ifndef _DEFAULT_SOURCE
#define _DEFAULT_SOURCE
#endif
#define NCURSES_WIDECHAR 1

#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <getopt.h>
#include <locale.h>
#include <math.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdarg.h>
#include <string.h>
#include <strings.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>
#include <wchar.h>

#include <ncurses.h>
#include <cairo.h>
#include <pango/pangocairo.h>
#include <wayland-client.h>
#include <wayland-egl.h>
#include <epoxy/gl.h>
#include <epoxy/egl.h>
#include <gtk/gtk.h>
#include <gdk/gdkwayland.h>
#include <gtk-layer-shell/gtk-layer-shell.h>
#include <glib-unix.h>

#define NCMATRIX_VERSION "nc-matrix 1.0.0 (NC-UI high-perf Matrix engine)"
#define ATLAS_COLS 16
#define MAX_GLYPHS 256
#define PALETTE_SIZE 128
#define PALETTE_COLORS 63
#define BG_R 0.00f
#define BG_G 0.00f
#define BG_B 0.00f
#define BG_ARGB32 0xFF000000u
#define WP_BASE_TICK_MS 5
#define MAX_TRAILS_PER_COL 12
#define MAX_SIM_ROWS 1024

#define TRAIL_ACTIVE   0x01u
#define TRAIL_RAINBOW  0x02u
#define TRAIL_MIRROR   0x04u
#define TRAIL_HAS_HEAD 0x08u

enum {
    HEAD_MATCH = 0,
    HEAD_WHITE = 1,
    HEAD_BLEND = 2
};

enum {
    OVERLAP_HYBRID = 0,
    OVERLAP_PARALLAX = 1,
    OVERLAP_ADDITIVE = 2
};

enum {
    DECAY_EXP = 0,
    DECAY_LINEAR = 1
};

enum {
    SHIMMER_OFF = 0,
    SHIMMER_LOW = 1,
    SHIMMER_MED = 2,
    SHIMMER_HIGH = 3,
    SHIMMER_INSANE = 4
};

enum {
    MIRROR_OFF = 0,
    MIRROR_ON = 1,
    MIRROR_RANDOM = 2
};

enum {
    TAIL_SHORT = 0,
    TAIL_NORMAL = 1,
    TAIL_LONG = 2
};

enum {
    CS_ASCII = 0,
    CS_KATAKANA = 1,
    CS_BINARY = 2,
    CS_HEX = 3,
    CS_LAMBDA = 4
};

/* Compact 16-byte autonomous trail state */
typedef struct {
    int16_t  head_row;
    int16_t  len;
    uint16_t step_ms;
    uint16_t accum_ms;
    uint8_t  color_id;     /* palette index or rainbow per-trail seed */
    uint8_t  charset;      /* CS_* */
    uint8_t  flags;        /* TRAIL_* */
    uint8_t  head_mix;     /* 0..255 intrinsic random white-blend draw */
    uint8_t  bright_u8;    /* 28..255 speed-linked max brightness */
    uint8_t  is_bold;      /* 0 or 1 */
    uint8_t  speed_rel_u8; /* 0..255 relative speed percentile */
    uint8_t  head_wh_u8;   /* 0..255 precomputed HEAD_BLEND white weight */
} MatrixTrail;

typedef struct {
    int cols;
    int lines;
    int col_step;     /* 2 = classic gutter, 1 = dense */
    int n_active;     /* (cols + col_step - 1) / col_step */
    int glyph_stride; /* lines */
    uint32_t last_tty_cfg_tag;
    MatrixTrail *trails;       /* n_active * MAX_TRAILS_PER_COL */
    uint16_t    *trail_glyphs; /* n_active * MAX_TRAILS_PER_COL * glyph_stride */
    uint8_t     *col_dirty;    /* n_active */
    int32_t     *spawn_cd_ms;  /* n_active */
    uint64_t    *rngs;         /* n_active */
    uint64_t     total_spawns;
    bool         draining;
    uint8_t      exp_decay_lut[256];
    uint8_t      lin_decay_lut[256];
    bool         luts_ready;
} MatrixSim;

typedef struct {
    int wallpaper;
    int benchmark_frames;
    int software;
    int asynch;
    int fade;
    int bold;       /* 0: default/none, 1: -b partial, 2: -B all, -1: -n none */
    int charset;    /* CS_* */
    int oldstyle;
    int screensaver;
    int console;
    int xwindow;
    int force_term;
    int lock;
    int rainbow;
    int changes;
    int update;          /* cmatrix -u delay (0..20 -> delay*10ms) */
    int speed;           /* nc-ui speed (1..10, higher = faster) */
    float spawn_rate;    /* trail spawn frequency multiplier (0.0..3.0, 1.0 = default) */
    int head_mode;       /* HEAD_WHITE, HEAD_MATCH, or HEAD_BLEND */
    float speed_variance;/* per-trail speed spread around speed (0.0..10.0) */
    int has_speed_var;
    int overlap_mode;    /* OVERLAP_HYBRID, OVERLAP_PARALLAX, OVERLAP_ADDITIVE */
    float depth_parallax;/* 0.0..1.0: slowness-to-darkness luminescence attenuation */
    float glow;          /* CRT phosphor halo intensity (0.0..1.0) */
    int decay_mode;      /* DECAY_EXP or DECAY_LINEAR */
    int shimmer_mode;    /* SHIMMER_OFF..SHIMMER_INSANE */
    int has_shimmer;
    int mirror_mode;     /* MIRROR_OFF, MIRROR_ON, MIRROR_RANDOM */
    int tail_mode;       /* TAIL_SHORT, TAIL_NORMAL, TAIL_LONG */
    int dense_cols;      /* 0: classic 1-on-1-off, 1: dense all columns */
    int has_u;
    int mcolor;     /* 0..62 palette index */
    uint8_t custom_rgb[3];
    bool has_custom_rgb;
    int font_size;
    int req_w;
    int req_h;
    int req_x;
    int req_y;
    int has_pos;
    int paused;
    char msg[256];
    char output_name[64];
    char pid_file[512];
    char ipc_socket[512];
    char *tty;
} Config;

typedef struct {
    uint16_t codepoints[MAX_GLYPHS]; /* 1..n_glyphs; slot 0 is blank */
    int      n_glyphs;
    int      cell_w;
    int      cell_h;
    int      scale;
    int      font_size;
    int      atlas_w;
    int      atlas_h;
    int      n_slots;    /* 1 + 2 * n_glyphs (normal then bold) */
    uint8_t *atlas_rg;   /* tight RG8 buffer (R=sharp glyph, G=separable blur): atlas_w * atlas_h * 2 */
    uint8_t  slot_latin1[256];
    uint8_t  slot_kata[64];
    uint8_t  slot_lambda;
} GlyphAtlas;

typedef struct {
    Config      cfg;
    Config      pending_cfg;
    bool        has_pending_cfg;
    MatrixSim   sim;
    GlyphAtlas  atlas;

    /* Native Wayland EGL + GPU state */
    GtkWidget              *sw_area;
    struct wl_display      *wl_dpy;
    struct wl_compositor   *wl_comp;
    struct wl_subcompositor *wl_subcomp;
    struct wl_surface      *wl_parent_surf;
    struct wl_surface      *wl_surf;
    struct wl_subsurface   *wl_sub;
    struct wl_egl_window   *egl_win;
    EGLDisplay              egl_dpy;
    EGLContext              egl_ctx;
    EGLSurface              egl_surf;
    int                     egl_w;
    int                     egl_h;
    bool                    egl_use_es;
    GLuint      vao;
    GLuint      prog;
    GLuint      tex_atlas;
    GLuint      tex_grid;
    GLint       u_view_size;
    GLint       u_cell_size;
    GLint       u_grid_size;
    GLint       u_palette;
    GLint       u_bg;
    GLint       u_glow;
    bool        gl_ready;
    bool        gl_failed;
    bool        gl_state_dirty;
    bool        glow_dirty;
    uint8_t    *grid_rgba;
    int         grid_cap;
    int         dirty_col_min;
    int         dirty_col_max;

    /* Software & palette state */
    cairo_surface_t *sw_surf;
    uint32_t        *prev_packed;
    int              prev_cap;
    float            palette_rgb[PALETTE_SIZE][3];
    uint8_t          palette_u8[PALETTE_SIZE][3];
    uint32_t         lut[PALETTE_SIZE][256];
    int              next_custom_slot;
    bool             palette_dirty;

    /* Runtime lifecycle */
    GtkWidget  *win;
    guint       timer_id;
    int         timer_interval_ms;
    int64_t     last_tick_ns;
    int         sock_fd;
    guint       sock_watch;
    ino_t       sock_ino;
    bool        running;
} WallpaperApp;

static volatile sig_atomic_t g_tty_sig = 0;

static inline uint64_t rng_next(uint64_t *s) {
    uint64_t x = *s;
    x ^= x >> 12;
    x ^= x << 25;
    x ^= x >> 27;
    *s = x;
    return x * 0x2545F4914F6CDD1DULL;
}

static inline uint32_t rng_u32(uint64_t *s, uint32_t mod) {
    return (uint32_t)(((uint64_t)(uint32_t)(rng_next(s) >> 32) * (uint64_t)mod) >> 32);
}

static int color_from_name(const char *name) {
    if (!name) return COLOR_GREEN;
    if (!strcasecmp(name, "green"))   return COLOR_GREEN;
    if (!strcasecmp(name, "red"))     return COLOR_RED;
    if (!strcasecmp(name, "blue"))    return COLOR_BLUE;
    if (!strcasecmp(name, "white"))   return COLOR_WHITE;
    if (!strcasecmp(name, "yellow"))  return COLOR_YELLOW;
    if (!strcasecmp(name, "cyan"))    return COLOR_CYAN;
    if (!strcasecmp(name, "magenta")) return COLOR_MAGENTA;
    if (!strcasecmp(name, "black"))   return COLOR_BLACK;
    return -1;
}

static bool parse_hex_rgb(const char *s, uint8_t rgb[3]) {
    if (!s) return false;
    if (s[0] == '#') s++;
    if (strspn(s, "0123456789abcdefABCDEF") != 6 || s[6] != '\0') return false;
    unsigned long v = strtoul(s, NULL, 16);
    rgb[0] = (uint8_t)(v >> 16);
    rgb[1] = (uint8_t)(v >> 8);
    rgb[2] = (uint8_t)v;
    return true;
}

static int nearest_tty_color(const uint8_t rgb[3]) {
    int r = rgb[0] >= 96 ? 1 : 0;
    int g = rgb[1] >= 96 ? 1 : 0;
    int b = rgb[2] >= 96 ? 1 : 0;
    if (!r && !g && !b) return COLOR_WHITE;
    if (r && g && b) return COLOR_WHITE;
    if (r && g) return COLOR_YELLOW;
    if (g && b) return COLOR_CYAN;
    if (r && b) return COLOR_MAGENTA;
    if (r) return COLOR_RED;
    if (g) return COLOR_GREEN;
    return COLOR_BLUE;
}

static int charset_from_name(const char *name) {
    if (!name) return CS_KATAKANA;
    if (!strcasecmp(name, "katakana")) return CS_KATAKANA;
    if (!strcasecmp(name, "ascii"))    return CS_ASCII;
    if (!strcasecmp(name, "binary"))   return CS_BINARY;
    if (!strcasecmp(name, "hex"))      return CS_HEX;
    if (!strcasecmp(name, "lambda"))   return CS_LAMBDA;
    return CS_ASCII;
}

static const int k_rainbow_colors[6] = {
    COLOR_GREEN, COLOR_BLUE, COLOR_BLACK, COLOR_YELLOW, COLOR_CYAN, COLOR_MAGENTA
};

static inline int speed_to_ms(int spd) {
    static const int k_speed_ms[10] = {520, 420, 330, 260, 200, 150, 110, 75, 45, 20};
    if (spd < 1) spd = 1;
    if (spd > 10) spd = 10;
    return k_speed_ms[spd - 1];
}

static inline uint16_t pick_glyph_cs(int charset, const Config *cfg, uint64_t *rng) {
    static const char k_hex[] = "0123456789ABCDEF";
    switch (charset) {
    case CS_KATAKANA: {
        uint32_t v = rng_u32(rng, (uint32_t)(0xFF9D - 0xFF66 + 10));
        if (v < (uint32_t)(0xFF9D - 0xFF66))
            return (uint16_t)(0xFF66 + v);
        return (uint16_t)('0' + (v - (0xFF9D - 0xFF66)));
    }
    case CS_BINARY:
        return (uint16_t)('0' + rng_u32(rng, 2));
    case CS_HEX:
        return (uint16_t)k_hex[rng_u32(rng, 16)];
    case CS_LAMBDA:
        return 0x03BBu;
    case CS_ASCII:
    default:
        if (cfg->console || cfg->xwindow)
            return (uint16_t)(166u + rng_u32(rng, 51u));
        return (uint16_t)(33u + rng_u32(rng, 90u));
    }
}

static bool parse_float_clamped(const char *s, float lo, float hi, float *out) {
    if (!s || !out) return false;
    while (*s && isspace((unsigned char)*s)) s++;
    if (!*s || *s == '-') {
        if (*s == '-') *out = lo;
        return false;
    }
    char buf[48];
    size_t i = 0;
    while (s[i] && i + 1 < sizeof(buf)) {
        buf[i] = (s[i] == ',') ? '.' : s[i];
        i++;
    }
    buf[i] = '\0';
    char *end = NULL;
    float v = strtof(buf, &end);
    if (end == buf) return false;
    while (*end && isspace((unsigned char)*end)) end++;
    if (*end != '\0' || !isfinite(v)) return false;
    *out = fminf(hi, fmaxf(lo, v));
    return true;
}

typedef struct {
    const char *name;
    int val;
} NameVal;

static bool parse_enum_table(const char *s, const NameVal *tbl, int *out) {
    if (!s || !out) return false;
    while (*s && isspace((unsigned char)*s)) s++;
    for (const NameVal *p = tbl; p->name; p++) {
        if (!strcasecmp(s, p->name)) {
            *out = p->val;
            return true;
        }
    }
    return false;
}

static const NameVal k_head_tbl[] = {
    {"white", HEAD_WHITE}, {"1", HEAD_WHITE}, {"on", HEAD_WHITE}, {"true", HEAD_WHITE},
    {"blend", HEAD_BLEND}, {"mix", HEAD_BLEND}, {"random", HEAD_BLEND}, {"fade", HEAD_BLEND}, {"2", HEAD_BLEND},
    {"match", HEAD_MATCH}, {"color", HEAD_MATCH}, {"trail", HEAD_MATCH}, {"shader", HEAD_MATCH}, {"0", HEAD_MATCH}, {"off", HEAD_MATCH}, {"false", HEAD_MATCH},
    {NULL, 0}
};
static const NameVal k_overlap_tbl[] = {
    {"hybrid", OVERLAP_HYBRID}, {"0", OVERLAP_HYBRID},
    {"parallax", OVERLAP_PARALLAX}, {"1", OVERLAP_PARALLAX},
    {"additive", OVERLAP_ADDITIVE}, {"screen", OVERLAP_ADDITIVE}, {"2", OVERLAP_ADDITIVE},
    {NULL, 0}
};
static const NameVal k_decay_tbl[] = {
    {"exponential", DECAY_EXP}, {"exp", DECAY_EXP}, {"crt", DECAY_EXP}, {"0", DECAY_EXP},
    {"linear", DECAY_LINEAR}, {"classic", DECAY_LINEAR}, {"1", DECAY_LINEAR},
    {NULL, 0}
};
static const NameVal k_shimmer_tbl[] = {
    {"off", SHIMMER_OFF}, {"none", SHIMMER_OFF}, {"0", SHIMMER_OFF}, {"false", SHIMMER_OFF},
    {"low", SHIMMER_LOW}, {"1", SHIMMER_LOW},
    {"med", SHIMMER_MED}, {"medium", SHIMMER_MED}, {"2", SHIMMER_MED},
    {"high", SHIMMER_HIGH}, {"3", SHIMMER_HIGH}, {"on", SHIMMER_HIGH}, {"true", SHIMMER_HIGH},
    {"insane", SHIMMER_INSANE}, {"max", SHIMMER_INSANE}, {"4", SHIMMER_INSANE},
    {NULL, 0}
};
static const NameVal k_mirror_tbl[] = {
    {"off", MIRROR_OFF}, {"normal", MIRROR_OFF}, {"0", MIRROR_OFF}, {"false", MIRROR_OFF},
    {"on", MIRROR_ON}, {"all", MIRROR_ON}, {"mirror", MIRROR_ON}, {"1", MIRROR_ON}, {"true", MIRROR_ON},
    {"random", MIRROR_RANDOM}, {"mix", MIRROR_RANDOM}, {"2", MIRROR_RANDOM},
    {NULL, 0}
};
static const NameVal k_tail_tbl[] = {
    {"short", TAIL_SHORT}, {"0", TAIL_SHORT},
    {"normal", TAIL_NORMAL}, {"medium", TAIL_NORMAL}, {"1", TAIL_NORMAL},
    {"long", TAIL_LONG}, {"2", TAIL_LONG},
    {NULL, 0}
};
static const NameVal k_bool_tbl[] = {
    {"1", 1}, {"on", 1}, {"true", 1}, {"yes", 1}, {"dense", 1},
    {"0", 0}, {"off", 0}, {"false", 0}, {"no", 0}, {"classic", 0},
    {NULL, 0}
};

static bool parse_parallax_strength(const char *s, float *out) {
    if (!s || !out) return false;
    while (*s && isspace((unsigned char)*s)) s++;
    if (!*s) return false;
    if (!strcasecmp(s, "on") || !strcasecmp(s, "true") || !strcasecmp(s, "yes")) {
        *out = 0.50f;
        return true;
    }
    if (!strcasecmp(s, "off") || !strcasecmp(s, "false") || !strcasecmp(s, "no")) {
        *out = 0.0f;
        return true;
    }
    float v = 0.50f;
    if (!parse_float_clamped(s, 0.0f, 100.0f, &v))
        return false;
    if (v > 1.0f) v *= 0.01f;
    *out = fminf(1.0f, fmaxf(0.0f, v));
    return true;
}

static inline int sim_base_step_ms(const Config *cfg) {
    if (cfg->has_u) {
        int d = cfg->update * 10;
        return (d < 5) ? 5 : d;
    }
    return speed_to_ms(cfg->speed);
}

static inline float effective_speed_variance(const Config *cfg) {
    if (cfg->has_speed_var)
        return cfg->speed_variance;
    return cfg->asynch ? 5.0f : 0.0f;
}

static int compute_optimal_timer_ms(const Config *cfg, bool draining) {
    if (draining)
        return 16;
    int base_ms = sim_base_step_ms(cfg);
    float var = effective_speed_variance(cfg);
    if (var <= 0.01f)
        return base_ms;
    float spread = powf(2.0f, var * 0.50f);
    int min_ms = (int)lroundf((float)base_ms / spread);
    int floor_ms = (var > 5.0f) ? 10 : 12;
    if (min_ms < floor_ms) min_ms = floor_ms;
    return (min_ms < base_ms) ? min_ms : base_ms;
}

static inline int sim_max_space(int lines, float rate) {
    if (lines < 1) lines = 1;
    if (rate <= 0.001f || fabsf(rate - 1.0f) < 1e-4f)
        return lines;
    if (rate < 1.0f) {
        float mean_s = (float)lines * ((1.0f / rate) - 0.5f);
        long max_sp = lroundf(2.0f * mean_s - 1.0f);
        if (max_sp < 1L) max_sp = 1L;
        if (max_sp > (long)lines * 40L) max_sp = (long)lines * 40L;
        return (int)max_sp;
    }
    float eff_t = fmaxf(5.0f, ((float)lines / rate) - 2.0f);
    float frac_len = 0.50f + 0.14f * fminf(1.0f, (rate - 1.0f) * 0.25f);
    float frac_sp = 1.0f - frac_len;
    float mean_s = eff_t * frac_sp;
    long max_sp = lroundf(2.0f * mean_s - 1.0f);
    if (max_sp < 1L) max_sp = 1L;
    return (int)max_sp;
}

static inline int sim_pick_space(int lines, float rate, uint64_t *rng) {
    uint32_t span = (uint32_t)sim_max_space(lines, rate);
    if (span < 1u) span = 1u;
    return (int)rng_u32(rng, span) + 1;
}

static inline int sim_pick_length(int lines, float rate, int tail_mode, uint64_t *rng) {
    int base_l;
    if (lines <= 3) {
        base_l = 3;
    } else if (rate <= 1.0001f) {
        base_l = (int)rng_u32(rng, (uint32_t)(lines - 3)) + 3;
    } else {
        float eff_t = fmaxf(5.0f, ((float)lines / rate) - 2.0f);
        float frac_len = 0.50f + 0.14f * fminf(1.0f, (rate - 1.0f) * 0.25f);
        float mean_l = eff_t * frac_len;
        long span = lroundf(2.0f * (mean_l - 3.0f) + 1.0f);
        if (span < 1L) span = 1L;
        if (span > (long)(lines - 3)) span = (long)(lines - 3);
        base_l = (int)rng_u32(rng, (uint32_t)span) + 3;
    }
    if (tail_mode == TAIL_SHORT)
        base_l = (base_l * 55 + 50) / 100;
    else if (tail_mode == TAIL_LONG)
        base_l = (base_l * 175 + 50) / 100;
    if (base_l < 3) base_l = 3;
    if (base_l > lines * 2) base_l = lines * 2;
    return base_l;
}

static void sim_ensure_luts(MatrixSim *s) {
    if (s->luts_ready) return;
    for (int i = 0; i < 256; i++) {
        float t = (float)i * (1.0f / 255.0f);
        float lin = 1.0f - t;
        if (lin < 0.14f) lin = 0.14f;
        long lv = lroundf(lin * 0.90f * 255.0f);
        s->lin_decay_lut[i] = (uint8_t)((lv > 255L) ? 255 : (lv < 0L ? 0 : lv));

        float ex = 0.08f + 0.88f * expf(-3.15f * t);
        long ev = lroundf(ex * 255.0f);
        s->exp_decay_lut[i] = (uint8_t)((ev > 255L) ? 255 : (ev < 0L ? 0 : ev));
    }
    s->luts_ready = true;
}

static void sim_free(MatrixSim *s) {
    free(s->trails);
    free(s->trail_glyphs);
    free(s->col_dirty);
    free(s->spawn_cd_ms);
    free(s->rngs);
    memset(s, 0, sizeof(*s));
}

static inline void update_trail_luminescence(MatrixTrail *tr, const Config *cfg) {
    float rel = (float)tr->speed_rel_u8 * (1.0f / 255.0f);
    float abs_rel = 1.0f - fmaxf(0.0f, fminf(1.0f, ((float)tr->step_ms - 20.0f) * (1.0f / 380.0f)));
    float var = effective_speed_variance(cfg);
    float eff_rel = (var > 0.01f) ? (0.78f * rel + 0.22f * abs_rel) : abs_rel;
    float s = fmaxf(0.0f, fminf(1.0f, cfg->depth_parallax)) * 2.0f;
    float atten, min_br;
    if (s <= 1.0f) {
        atten = s * (1.0f - eff_rel);
        min_br = 28.0f;
    } else {
        atten = 1.0f - powf(fmaxf(0.0f, eff_rel), s);
        min_br = 28.0f - (s - 1.0f) * 16.0f;
    }
    int min_i = (int)lroundf(min_br);
    int br = (int)lroundf(255.0f - atten * (255.0f - min_br));
    tr->bright_u8 = (uint8_t)(br < min_i ? min_i : (br > 255 ? 255 : br));
    float lum_n = fmaxf(0.0f, fminf(1.0f, ((float)tr->bright_u8 - min_br) / (255.0f - min_br)));
    float range_f = (s <= 1.0f) ? (lum_n * lum_n) : powf(lum_n, 1.0f + s);
    int wh = (int)lroundf(((float)tr->head_mix / 255.0f) * range_f * 255.0f);
    tr->head_wh_u8 = (uint8_t)(wh < 0 ? 0 : (wh > 255 ? 255 : wh));
    if (cfg->bold == -1)
        tr->is_bold = 0u;
    else if (s >= 0.30f && var > 0.01f)
        tr->is_bold = (eff_rel >= 0.45f * fminf(1.0f, s)) ? 1u : 0u;
    else if (cfg->bold == 2)
        tr->is_bold = 1u;
}

static void sim_spawn_trail(MatrixSim *s, const Config *cfg, int k, int ti, int init_head_row, uint64_t *rng) {
    MatrixTrail *tr = &s->trails[(size_t)k * MAX_TRAILS_PER_COL + (size_t)ti];
    uint16_t *tg = &s->trail_glyphs[((size_t)k * MAX_TRAILS_PER_COL + (size_t)ti) * (size_t)s->glyph_stride];

    uint8_t cs = (uint8_t)cfg->charset;
    bool is_rb = (cfg->rainbow != 0);
    int len = sim_pick_length(s->lines, cfg->spawn_rate, cfg->tail_mode, rng);

    int base_ms = sim_base_step_ms(cfg);
    float var = effective_speed_variance(cfg);
    uint16_t step_ms;
    float rel = 0.5f;
    uint8_t is_bold = (cfg->bold == 2) ? 1u : ((cfg->bold == 1) ? (uint8_t)rng_u32(rng, 2u) : 0u);

    if (var <= 0.01f) {
        step_ms = (uint16_t)base_ms;
    } else {
        float spread = powf(2.0f, var * 0.50f);
        rel = (float)rng_u32(rng, 1024u) * (1.0f / 1023.0f);
        float u = rel * 2.0f - 1.0f;
        int min_ms = (var > 5.0f) ? 10 : 12;
        int max_ms = (int)lroundf((float)base_ms * (var <= 5.0f ? 2.1f : (2.1f + (var - 5.0f) * 0.48f)));
        int ms = (int)lroundf((float)base_ms * powf(spread, -u));
        if (ms < min_ms) ms = min_ms;
        if (ms > max_ms) ms = max_ms;
        step_ms = (uint16_t)ms;
    }

    uint8_t flags = TRAIL_ACTIVE;
    if (is_rb) flags |= TRAIL_RAINBOW;
    if (cfg->mirror_mode == MIRROR_ON)
        flags |= TRAIL_MIRROR;
    if (!cfg->oldstyle || rng_u32(rng, 3u) == 1u)
        flags |= TRAIL_HAS_HEAD;

    tr->head_row     = (int16_t)init_head_row;
    tr->len          = (int16_t)len;
    tr->step_ms      = step_ms;
    tr->accum_ms     = 0;
    tr->color_id     = is_rb ? (uint8_t)rng_u32(rng, 251u) : (uint8_t)(cfg->mcolor % PALETTE_COLORS);
    tr->charset      = cs;
    tr->flags        = flags;
    tr->head_mix     = (uint8_t)rng_u32(rng, 256u);
    tr->is_bold      = is_bold;
    tr->speed_rel_u8 = (uint8_t)lroundf(rel * 255.0f);
    update_trail_luminescence(tr, cfg);

    int r_lo = init_head_row - len;
    if (r_lo < 0) r_lo = 0;
    int r_hi = (init_head_row < s->lines) ? init_head_row : (s->lines - 1);
    for (int r = r_lo; r <= r_hi; r++)
        tg[r] = pick_glyph_cs(cs, cfg, rng);

    s->total_spawns++;
}

static void sim_init(MatrixSim *s, int lines, int cols, const Config *cfg) {
    if (lines < 4) lines = 4;
    if (lines > MAX_SIM_ROWS) lines = MAX_SIM_ROWS;
    if (cols < 4) cols = 4;

    sim_ensure_luts(s);
    int col_step = (cfg && cfg->dense_cols) ? 1 : 2;
    int n_active = (cols + col_step - 1) / col_step;

    s->lines = lines;
    s->cols = cols;
    s->col_step = col_step;
    s->n_active = n_active;
    s->glyph_stride = lines;
    s->last_tty_cfg_tag = 0xFFFFFFFFu;
    s->draining = false;

    size_t n_slots = (size_t)n_active * MAX_TRAILS_PER_COL;
    s->trails       = realloc(s->trails,       n_slots * sizeof(MatrixTrail));
    s->trail_glyphs = realloc(s->trail_glyphs, n_slots * (size_t)lines * sizeof(uint16_t));
    s->col_dirty    = realloc(s->col_dirty,    (size_t)n_active * sizeof(uint8_t));
    s->spawn_cd_ms  = realloc(s->spawn_cd_ms,  (size_t)n_active * sizeof(int32_t));
    s->rngs         = realloc(s->rngs,         (size_t)n_active * sizeof(uint64_t));

    if (!s->trails || !s->trail_glyphs || !s->col_dirty || !s->spawn_cd_ms || !s->rngs) {
        fprintf(stderr, "nc-matrix: out of memory allocating grid (%dx%d)\n", cols, lines);
        exit(1);
    }
    memset(s->trails, 0, n_slots * sizeof(MatrixTrail));
    memset(s->col_dirty, 1, (size_t)n_active * sizeof(uint8_t));

    int base_ms = cfg ? sim_base_step_ms(cfg) : 35;
    float rate = cfg ? cfg->spawn_rate : 1.0f;
    uint64_t seed_base = ((uint64_t)time(NULL) << 32) ^ (uint64_t)getpid() ^ 0x9E3779B97F4A7C15ULL;
    for (int k = 0; k < n_active; k++) {
        uint64_t rng = seed_base ^ ((uint64_t)(k + 1) * 0xBF58476D1CE4E5B9ULL);
        rng_next(&rng);
        int gap = sim_pick_space(lines, rate, &rng);
        s->spawn_cd_ms[k] = (int32_t)(gap * base_ms);
        s->rngs[k] = rng ? rng : 1ULL;
    }
    s->total_spawns = 0;
}

static inline bool sim_step_column_dt(MatrixSim *s, const Config *cfg, int k, int dt_ms) {
    const int lines = s->lines;
    uint64_t rng = s->rngs[k];
    MatrixTrail *col_tr = &s->trails[(size_t)k * MAX_TRAILS_PER_COL];
    uint16_t *col_gl = &s->trail_glyphs[(size_t)k * MAX_TRAILS_PER_COL * (size_t)s->glyph_stride];

    int shim = cfg->has_shimmer ? cfg->shimmer_mode : (cfg->changes ? SHIMMER_HIGH : cfg->shimmer_mode);

    bool changed = false;
    int free_slot = -1;
    int min_head = 32767;
    int min_tail_top = 32767;

    for (int ti = 0; ti < MAX_TRAILS_PER_COL; ti++) {
        MatrixTrail *tr = &col_tr[ti];
        if (!(tr->flags & TRAIL_ACTIVE)) {
            if (free_slot < 0) free_slot = ti;
            continue;
        }
        uint16_t eff_ms = tr->step_ms;
        if (s->draining && eff_ms > 18u) eff_ms = 18u;
        uint32_t acc = (uint32_t)tr->accum_ms + (uint32_t)dt_ms;
        int guard = 0;
        uint16_t *tg = &col_gl[(size_t)ti * (size_t)s->glyph_stride];

        while (acc >= eff_ms && guard++ < 8) {
            acc -= eff_ms;
            tr->head_row++;
            changed = true;
            if (tr->head_row >= 0 && tr->head_row < lines) {
                tg[tr->head_row] = pick_glyph_cs(tr->charset, cfg, &rng);
            }
            if (shim != SHIMMER_OFF && !cfg->oldstyle && tr->head_row > 1) {
                int n_mut = (shim == SHIMMER_INSANE) ? 4
                          : (shim == SHIMMER_HIGH)   ? 2
                          : (shim == SHIMMER_MED)    ? 1
                          : ((rng_u32(&rng, 2u) == 0u) ? 1 : 0);
                if (n_mut > 0) {
                    int r_lo = (int)tr->head_row - (int)tr->len;
                    if (r_lo < 0) r_lo = 0;
                    int r_hi = ((int)tr->head_row - 1 < lines - 1) ? ((int)tr->head_row - 1) : (lines - 1);
                    if (r_hi >= r_lo) {
                        uint32_t span_r = (uint32_t)(r_hi - r_lo + 1);
                        for (int mi = 0; mi < n_mut; mi++) {
                            int mr = r_lo + (int)rng_u32(&rng, span_r);
                            tg[mr] = pick_glyph_cs(tr->charset, cfg, &rng);
                        }
                    }
                }
            }
            if ((int)tr->head_row - (int)tr->len >= lines) {
                tr->flags = 0;
                if (free_slot < 0) free_slot = ti;
                break;
            }
        }
        tr->accum_ms = (uint16_t)acc;

        if (tr->flags & TRAIL_ACTIVE) {
            if (tr->head_row < min_head) min_head = tr->head_row;
            int t_top = (int)tr->head_row - (int)tr->len;
            if (t_top < min_tail_top) min_tail_top = t_top;
        }
    }

    const bool no_spawn = s->draining || (cfg->spawn_rate <= 0.001f);
    if (!no_spawn) {
        float var = effective_speed_variance(cfg);
        bool can_tick_cd = (var > 0.01f) || (min_tail_top >= 1);
        if (can_tick_cd)
            s->spawn_cd_ms[k] -= dt_ms;

        if (s->spawn_cd_ms[k] <= 0 && free_slot >= 0 && min_head >= 3) {
            sim_spawn_trail(s, cfg, k, free_slot, 0, &rng);
            int base_ms = sim_base_step_ms(cfg);
            int gap = sim_pick_space(lines, cfg->spawn_rate, &rng);
            int new_len = col_tr[free_slot].len;
            int wait_rows = (var > 0.01f) ? (gap + new_len / 2) : gap;
            if (wait_rows < 1) wait_rows = 1;
            s->spawn_cd_ms[k] = (int32_t)(wait_rows * base_ms);
            changed = true;
        }
    }

    s->rngs[k] = rng;
    if (changed)
        s->col_dirty[k] = 1;
    return changed;
}

static bool sim_is_screen_empty(const MatrixSim *s, const Config *cfg) {
    (void)cfg;
    size_t n_slots = (size_t)s->n_active * MAX_TRAILS_PER_COL;
    for (size_t i = 0; i < n_slots; i++) {
        if (s->trails[i].flags & TRAIL_ACTIVE)
            return false;
    }
    return true;
}

static bool sim_step_dt(MatrixSim *s, const Config *cfg, int dt_ms) {
    if (cfg->paused || dt_ms <= 0) return false;
    const int n_active = s->n_active;
    bool any_changed = false;
    for (int k = 0; k < n_active; k++) {
        if (sim_step_column_dt(s, cfg, k, dt_ms))
            any_changed = true;
    }
    return any_changed;
}

/* ============================================================================
 * Palette & Atlas (Pre-rendered ONCE via PangoCairo)
 * ============================================================================ */

static const float k_base_palette_rgb[18][3] = {
    [0]  = {BG_R, BG_G, BG_B},
    [1]  = {0.95f, 1.00f, 0.97f}, /* Bright white head */
    /* Normal intensity (0.85x) indexed by 2 + color_id */
    [2 + COLOR_BLACK]   = {0.10f, 0.14f, 0.18f},
    [2 + COLOR_RED]     = {0.85f, 0.14f, 0.21f},
    [2 + COLOR_GREEN]   = {0.00f, 0.85f, 0.38f},
    [2 + COLOR_YELLOW]  = {0.85f, 0.78f, 0.08f},
    [2 + COLOR_BLUE]    = {0.25f, 0.48f, 0.85f},
    [2 + COLOR_MAGENTA] = {0.85f, 0.14f, 0.52f},
    [2 + COLOR_CYAN]    = {0.00f, 0.80f, 0.85f},
    [2 + COLOR_WHITE]   = {0.80f, 0.82f, 0.85f},
    /* Bold intensity (1.0x) indexed by 10 + color_id */
    [10 + COLOR_BLACK]   = {0.22f, 0.26f, 0.32f},
    [10 + COLOR_RED]     = {1.00f, 0.20f, 0.28f},
    [10 + COLOR_GREEN]   = {0.00f, 1.00f, 0.45f},
    [10 + COLOR_YELLOW]  = {1.00f, 0.92f, 0.10f},
    [10 + COLOR_BLUE]    = {0.34f, 0.62f, 1.00f},
    [10 + COLOR_MAGENTA] = {1.00f, 0.20f, 0.65f},
    [10 + COLOR_CYAN]    = {0.00f, 0.94f, 1.00f},
    [10 + COLOR_WHITE]   = {0.96f, 0.98f, 1.00f},
};

static inline uint32_t premul_blend_argb32(float r, float g, float b, uint8_t a) {
    float t = (float)a / 255.0f;
    uint32_t rr = (uint32_t)lroundf((BG_R + (r - BG_R) * t) * 255.0f);
    uint32_t gg = (uint32_t)lroundf((BG_G + (g - BG_G) * t) * 255.0f);
    uint32_t bb = (uint32_t)lroundf((BG_B + (b - BG_B) * t) * 255.0f);
    if (rr > 255u) rr = 255u;
    if (gg > 255u) gg = 255u;
    if (bb > 255u) bb = 255u;
    return 0xFF000000u | (rr << 16) | (gg << 8) | bb;
}

static inline uint8_t f_to_u8(float x) {
    int v = (int)lroundf(fminf(1.0f, fmaxf(0.0f, x)) * 255.0f);
    return (uint8_t)v;
}

static void set_palette_color(WallpaperApp *app, int cid, float r, float g, float b) {
    if (cid < 0 || cid >= PALETTE_COLORS) return;
    int norm_i = 2 + cid;
    int bold_i = 65 + cid;
    app->palette_rgb[norm_i][0] = r * 0.85f;
    app->palette_rgb[norm_i][1] = g * 0.85f;
    app->palette_rgb[norm_i][2] = b * 0.85f;
    app->palette_rgb[bold_i][0] = r;
    app->palette_rgb[bold_i][1] = g;
    app->palette_rgb[bold_i][2] = b;
    for (int ch = 0; ch < 3; ch++) {
        app->palette_u8[norm_i][ch] = f_to_u8(app->palette_rgb[norm_i][ch]);
        app->palette_u8[bold_i][ch] = f_to_u8(app->palette_rgb[bold_i][ch]);
    }
    for (int a = 0; a < 256; a++) {
        app->lut[norm_i][a] = premul_blend_argb32(app->palette_rgb[norm_i][0], app->palette_rgb[norm_i][1], app->palette_rgb[norm_i][2], (uint8_t)a);
        app->lut[bold_i][a] = premul_blend_argb32(app->palette_rgb[bold_i][0], app->palette_rgb[bold_i][1], app->palette_rgb[bold_i][2], (uint8_t)a);
    }
    app->palette_dirty = true;
}

static int register_custom_hex_color(WallpaperApp *app, Config *cfg, const uint8_t rgb[3]) {
    int slot = 8 + (app->next_custom_slot++ & 7);
    set_palette_color(app, slot, (float)rgb[0] / 255.0f, (float)rgb[1] / 255.0f, (float)rgb[2] / 255.0f);
    if (cfg) {
        memcpy(cfg->custom_rgb, rgb, 3);
        cfg->has_custom_rgb = true;
        cfg->mcolor = slot;
        cfg->rainbow = 0;
    }
    return slot;
}

static inline void gen_vivid_rainbow_rgb(uint64_t *rng, float *or_f, float *og_f, float *ob_f) {
    uint32_t rv = rng_u32(rng, 256u);
    uint32_t gv = rng_u32(rng, 256u);
    uint32_t bv = rng_u32(rng, 256u);
    if (rv < 0xCCu && gv < 0xCCu && bv < 0xCCu) {
        uint32_t boost = 0xCCu + rng_u32(rng, 256u - 0xCCu);
        uint32_t ch = rng_u32(rng, 3u);
        if (ch == 0u) rv = boost;
        else if (ch == 1u) gv = boost;
        else bv = boost;
    }
    *or_f = (float)rv / 255.0f;
    *og_f = (float)gv / 255.0f;
    *ob_f = (float)bv / 255.0f;
}

static void init_app_palette(WallpaperApp *app) {
    memset(app->palette_rgb, 0, sizeof(app->palette_rgb));
    memset(app->palette_u8, 0, sizeof(app->palette_u8));
    memcpy(app->palette_rgb[0], k_base_palette_rgb[0], sizeof(float) * 3);
    memcpy(app->palette_rgb[1], k_base_palette_rgb[1], sizeof(float) * 3);
    for (int ch = 0; ch < 3; ch++) {
        app->palette_u8[0][ch] = f_to_u8(app->palette_rgb[0][ch]);
        app->palette_u8[1][ch] = f_to_u8(app->palette_rgb[1][ch]);
    }
    for (int cid = 0; cid < 8; cid++) {
        memcpy(app->palette_rgb[2 + cid],  k_base_palette_rgb[2 + cid],  sizeof(float) * 3);
        memcpy(app->palette_rgb[65 + cid], k_base_palette_rgb[10 + cid], sizeof(float) * 3);
        for (int ch = 0; ch < 3; ch++) {
            app->palette_u8[2 + cid][ch]  = f_to_u8(app->palette_rgb[2 + cid][ch]);
            app->palette_u8[65 + cid][ch] = f_to_u8(app->palette_rgb[65 + cid][ch]);
        }
    }
    for (int st = 0; st < 2; st++) {
        for (int a = 0; a < 256; a++)
            app->lut[st][a] = premul_blend_argb32(app->palette_rgb[st][0], app->palette_rgb[st][1], app->palette_rgb[st][2], (uint8_t)a);
    }
    for (int cid = 0; cid < 8; cid++) {
        int ni = 2 + cid, bi = 65 + cid;
        for (int a = 0; a < 256; a++) {
            app->lut[ni][a] = premul_blend_argb32(app->palette_rgb[ni][0], app->palette_rgb[ni][1], app->palette_rgb[ni][2], (uint8_t)a);
            app->lut[bi][a] = premul_blend_argb32(app->palette_rgb[bi][0], app->palette_rgb[bi][1], app->palette_rgb[bi][2], (uint8_t)a);
        }
    }
    uint64_t rng = ((uint64_t)time(NULL) << 32) ^ (uint64_t)getpid() ^ 0xA24BAED4963EE407ULL;
    rng_next(&rng);
    for (int cid = 8; cid < 16; cid++)
        set_palette_color(app, cid, 0.0f, 1.0f, 0.45f);
    for (int cid = 16; cid < PALETTE_COLORS; cid++) {
        float r, g, b;
        gen_vivid_rainbow_rgb(&rng, &r, &g, &b);
        set_palette_color(app, cid, r, g, b);
    }
    if (app->cfg.has_custom_rgb)
        register_custom_hex_color(app, &app->cfg, app->cfg.custom_rgb);
    app->palette_dirty = true;
}

static inline uint8_t atlas_lookup_slot(const GlyphAtlas *a, uint16_t cp) {
    if (cp < 256u) return a->slot_latin1[cp];
    if (cp >= 0xFF66u && cp < 0xFF9Du) return a->slot_kata[cp - 0xFF66u];
    if (cp == 0x03BBu) return a->slot_lambda;
    return 0;
}

static void atlas_add_cp(GlyphAtlas *a, uint16_t cp) {
    if (cp == 0 || cp == ' ' || atlas_lookup_slot(a, cp) != 0) return;
    if (a->n_glyphs + 1 >= MAX_GLYPHS) return;
    uint8_t idx = (uint8_t)(++a->n_glyphs); /* 1-based */
    a->codepoints[idx] = cp;
    if (cp < 256u)
        a->slot_latin1[cp] = idx;
    else if (cp >= 0xFF66u && cp < 0xFF9Du)
        a->slot_kata[cp - 0xFF66u] = idx;
    else if (cp == 0x03BBu)
        a->slot_lambda = idx;
}

static int utf8_encode(uint16_t cp, char *out) {
    if (cp < 0x80u) {
        out[0] = (char)cp;
        out[1] = '\0';
        return 1;
    } else if (cp < 0x800u) {
        out[0] = (char)(0xC0u | (cp >> 6));
        out[1] = (char)(0x80u | (cp & 0x3Fu));
        out[2] = '\0';
        return 2;
    } else {
        out[0] = (char)(0xE0u | (cp >> 12));
        out[1] = (char)(0x80u | ((cp >> 6) & 0x3Fu));
        out[2] = (char)(0x80u | (cp & 0x3Fu));
        out[3] = '\0';
        return 3;
    }
}

static void blur_slot_2pass(const uint8_t *src, int src_stride, int bx, int by, int cw, int ch,
                            uint8_t *dst_rg, int aw, uint8_t *buf_a, uint8_t *buf_b) {
    int rad = (cw >= 14) ? 3 : 2;
    for (int y = 0; y < ch; y++) {
        const uint8_t *srow = src + (size_t)(by + y) * (size_t)src_stride + (size_t)bx;
        memcpy(buf_a + (size_t)y * (size_t)cw, srow, (size_t)cw);
    }

    for (int pass = 0; pass < 2; pass++) {
        for (int y = 0; y < ch; y++) {
            const uint8_t *in_r = buf_a + (size_t)y * (size_t)cw;
            uint8_t *out_r = buf_b + (size_t)y * (size_t)cw;
            for (int x = 0; x < cw; x++) {
                uint32_t sum = 0;
                int cnt = 0;
                int x0 = (x - rad > 0) ? (x - rad) : 0;
                int x1 = (x + rad < cw - 1) ? (x + rad) : (cw - 1);
                for (int xx = x0; xx <= x1; xx++) { sum += in_r[xx]; cnt++; }
                out_r[x] = (uint8_t)(sum / (uint32_t)cnt);
            }
        }
        for (int y = 0; y < ch; y++) {
            uint8_t *out_r = buf_a + (size_t)y * (size_t)cw;
            int y0 = (y - rad > 0) ? (y - rad) : 0;
            int y1 = (y + rad < ch - 1) ? (y + rad) : (ch - 1);
            int cnt = y1 - y0 + 1;
            for (int x = 0; x < cw; x++) {
                uint32_t sum = 0;
                for (int yy = y0; yy <= y1; yy++)
                    sum += buf_b[(size_t)yy * (size_t)cw + (size_t)x];
                out_r[x] = (uint8_t)(sum / (uint32_t)cnt);
            }
        }
    }

    for (int y = 0; y < ch; y++) {
        const uint8_t *srow = src + (size_t)(by + y) * (size_t)src_stride + (size_t)bx;
        const uint8_t *brow = buf_a + (size_t)y * (size_t)cw;
        uint8_t *drow = dst_rg + ((size_t)(by + y) * (size_t)aw + (size_t)bx) * 2u;
        for (int x = 0; x < cw; x++) {
            uint32_t hv = ((uint32_t)brow[x] * 9u) / 5u;
            drow[x * 2 + 0] = srow[x];
            drow[x * 2 + 1] = (uint8_t)(hv > 255u ? 255u : hv);
        }
    }
}

static void atlas_build(GlyphAtlas *a, const Config *cfg, int scale) {
    free(a->atlas_rg);
    memset(a, 0, sizeof(*a));
    if (scale < 1) scale = 1;
    a->scale = scale;

    int fsize = cfg->font_size;
    if (fsize < 5) fsize = 5;
    if (fsize > 32) fsize = 32;
    a->font_size = fsize;

    /* Always populate glyphs across all charsets so hot-reloaded streams coexist */
    for (uint16_t cp = 0xFF66; cp < 0xFF9D; cp++) atlas_add_cp(a, cp);
    for (uint16_t cp = 33; cp < 127; cp++) atlas_add_cp(a, cp);
    atlas_add_cp(a, 0x03BBu);
    atlas_add_cp(a, 0x00B7u);
    if (cfg->msg[0]) {
        for (const char *p = cfg->msg; *p; p++)
            atlas_add_cp(a, (uint8_t)*p);
    }

    cairo_surface_t *tmp = cairo_image_surface_create(CAIRO_FORMAT_A8, 1, 1);
    cairo_t *tmp_cr = cairo_create(tmp);
    PangoLayout *play = pango_cairo_create_layout(tmp_cr);

    char font_norm_str[64], font_bold_str[64];
    int pt_scaled = fsize * scale;
    snprintf(font_norm_str, sizeof(font_norm_str), "monospace %d", pt_scaled);
    snprintf(font_bold_str, sizeof(font_bold_str), "monospace bold %d", pt_scaled);
    PangoFontDescription *desc_norm = pango_font_description_from_string(font_norm_str);
    PangoFontDescription *desc_bold = pango_font_description_from_string(font_bold_str);

    pango_layout_set_font_description(play, desc_bold);
    PangoRectangle ink, log;
    int max_w = 0, max_h = 0;
    const char *probes[] = {"M", "W", "0", "\xEF\xBD\xB1", "\xEF\xBE\x9D", "\xCE\xBB"};
    for (size_t i = 0; i < sizeof(probes) / sizeof(probes[0]); i++) {
        pango_layout_set_text(play, probes[i], -1);
        pango_layout_get_pixel_extents(play, &ink, &log);
        if (log.width > max_w) max_w = log.width;
        if (ink.width > max_w) max_w = ink.width;
        if (log.height > max_h) max_h = log.height;
    }

    a->cell_w = (max_w >= 5 * scale) ? max_w : (5 * scale);
    a->cell_h = (max_h >= 7 * scale) ? max_h : (7 * scale);
    if ((cfg->charset == CS_KATAKANA || cfg->wallpaper) && a->cell_w < (int)lround(pt_scaled * 0.78))
        a->cell_w = (int)lround(pt_scaled * 0.78);

    a->n_slots = 1 + 2 * a->n_glyphs;
    int atlas_rows = (a->n_slots + ATLAS_COLS - 1) / ATLAS_COLS;
    a->atlas_w = ATLAS_COLS * a->cell_w;
    a->atlas_h = atlas_rows * a->cell_h;

    cairo_surface_t *surf = cairo_image_surface_create(CAIRO_FORMAT_A8, a->atlas_w, a->atlas_h);
    cairo_t *cr = cairo_create(surf);
    cairo_set_operator(cr, CAIRO_OPERATOR_CLEAR);
    cairo_paint(cr);
    cairo_set_operator(cr, CAIRO_OPERATOR_OVER);
    cairo_set_source_rgba(cr, 1.0, 1.0, 1.0, 1.0);
    pango_cairo_update_layout(cr, play);
    for (int pass = 0; pass < 2; pass++) {
        pango_layout_set_font_description(play, pass ? desc_bold : desc_norm);
        int base_slot = pass ? a->n_glyphs : 0;
        for (int idx = 1; idx <= a->n_glyphs; idx++) {
            int slot = base_slot + idx;
            int ac = slot % ATLAS_COLS;
            int ar = slot / ATLAS_COLS;
            int bx = ac * a->cell_w;
            int by = ar * a->cell_h;

            char utf8[8];
            int len = utf8_encode(a->codepoints[idx], utf8);
            pango_layout_set_text(play, utf8, len);
            pango_layout_get_pixel_extents(play, &ink, &log);

            int ox = bx + (a->cell_w - log.width) / 2;
            int oy = by + (a->cell_h - log.height) / 2;
            if (ox < bx) ox = bx;
            if (oy < by) oy = by;

            cairo_save(cr);
            cairo_rectangle(cr, bx, by, a->cell_w, a->cell_h);
            cairo_clip(cr);
            cairo_move_to(cr, ox, oy);
            pango_cairo_show_layout(cr, play);
            cairo_restore(cr);
        }
    }
    cairo_surface_flush(surf);

    int stride = cairo_image_surface_get_stride(surf);
    const uint8_t *src = cairo_image_surface_get_data(surf);
    a->atlas_rg = calloc((size_t)a->atlas_w * (size_t)a->atlas_h * 2u, 1);
    size_t cell_px = (size_t)a->cell_w * (size_t)a->cell_h;
    uint8_t *buf_a = malloc(cell_px);
    uint8_t *buf_b = malloc(cell_px);
    if (!a->atlas_rg || !buf_a || !buf_b) {
        fprintf(stderr, "nc-matrix: OOM allocating RG8 atlas\n");
        exit(1);
    }

    for (int slot = 1; slot < a->n_slots; slot++) {
        int bx = (slot % ATLAS_COLS) * a->cell_w;
        int by = (slot / ATLAS_COLS) * a->cell_h;
        blur_slot_2pass(src, stride, bx, by, a->cell_w, a->cell_h, a->atlas_rg, a->atlas_w, buf_a, buf_b);
    }

    free(buf_b);
    free(buf_a);
    g_object_unref(play);
    cairo_destroy(cr);
    cairo_surface_destroy(surf);
    pango_font_description_free(desc_norm);
    pango_font_description_free(desc_bold);
    cairo_destroy(tmp_cr);
    cairo_surface_destroy(tmp);
}

static inline void composite_column(const MatrixSim *s, const Config *cfg, int k, int max_r,
                                    bool eight_col, uint16_t row_cp[MAX_SIM_ROWS],
                                    uint8_t row_cid[MAX_SIM_ROWS], uint8_t row_fade[MAX_SIM_ROWS],
                                    uint8_t row_wh[MAX_SIM_ROWS], uint8_t row_mir[MAX_SIM_ROWS],
                                    uint8_t row_flags[MAX_SIM_ROWS]) {
    const uint8_t *decay_lut = (cfg->decay_mode == DECAY_EXP) ? s->exp_decay_lut : s->lin_decay_lut;
    const int overlap = cfg->overlap_mode;
    const int mir_mode = cfg->mirror_mode;
    const MatrixTrail *col_tr = &s->trails[(size_t)k * MAX_TRAILS_PER_COL];
    const uint16_t *col_gl = &s->trail_glyphs[(size_t)k * MAX_TRAILS_PER_COL * (size_t)s->glyph_stride];
    uint32_t row_prio[MAX_SIM_ROWS];

    memset(row_flags, 0, (size_t)max_r);

    for (int ti = 0; ti < MAX_TRAILS_PER_COL; ti++) {
        const MatrixTrail *tr = &col_tr[ti];
        if (!(tr->flags & TRAIL_ACTIVE))
            continue;

        int r_end = (tr->head_row < max_r) ? tr->head_row : (max_r - 1);
        int r_start = (int)tr->head_row - (int)tr->len;
        if (r_start < 0) r_start = 0;
        if (r_start > r_end) continue;

        const uint16_t *tg = &col_gl[(size_t)ti * (size_t)s->glyph_stride];
        const bool tr_rb = (tr->flags & TRAIL_RAINBOW) != 0u;
        const uint32_t inv_spd = (tr->step_ms < 1000u) ? (1000u - tr->step_ms) : 1u;

        for (int r = r_start; r <= r_end; r++) {
            int dist = (int)tr->head_row - r;
            bool is_hd = (dist == 0) && ((tr->flags & TRAIL_HAS_HEAD) != 0u);
            uint16_t disp_cp = (cfg->oldstyle && is_hd) ? '&' : tg[r];
            if (cfg->charset == CS_LAMBDA && !cfg->oldstyle)
                disp_cp = 0x03BBu;

            uint8_t fade_u8;
            if (is_hd || !cfg->fade) {
                fade_u8 = tr->bright_u8;
            } else {
                uint32_t li = ((uint32_t)dist * 255u) / (uint32_t)(tr->len + 1);
                if (li > 255u) li = 255u;
                fade_u8 = (uint8_t)(((uint32_t)decay_lut[li] * (uint32_t)tr->bright_u8 + 127u) >> 8);
            }

            uint8_t is_bold = (uint8_t)(is_hd ? (cfg->bold != -1) : (tr->is_bold != 0));
            uint8_t cid;
            uint8_t wh_u8 = 0;
            if (is_hd && cfg->head_mode == HEAD_WHITE) {
                cid = eight_col ? (uint8_t)COLOR_WHITE : 255u;
            } else {
                if (tr_rb) {
                    uint32_t h = (uint32_t)r * 131u + (uint32_t)tr->color_id * 37u + (uint32_t)k * 17u;
                    cid = eight_col ? (uint8_t)k_rainbow_colors[h % 6u]
                                    : (uint8_t)(16u + (h % (uint32_t)(PALETTE_COLORS - 16)));
                } else {
                    cid = eight_col ? (uint8_t)(cfg->mcolor & 7)
                                    : (uint8_t)(cfg->mcolor % PALETTE_COLORS);
                }
                if (is_hd && cfg->head_mode == HEAD_BLEND)
                    wh_u8 = tr->head_wh_u8;
            }

            uint8_t mir = 0;
            if (mir_mode == MIRROR_ON)
                mir = 1;
            else if (mir_mode == MIRROR_RANDOM)
                mir = (uint8_t)((((uint32_t)disp_cp * 2654435761u) ^ (uint32_t)(r * 131 + k * 17)) & 1u);

            uint8_t fl = (uint8_t)(1u | (is_bold << 1) | ((is_hd ? 1u : 0u) << 2));
            if (!(row_flags[r] & 1u)) {
                row_flags[r] = fl;
                row_cp[r]    = disp_cp;
                row_cid[r]   = cid;
                row_fade[r]  = fade_u8;
                row_wh[r]    = wh_u8;
                row_mir[r]   = mir;
                row_prio[r]  = (overlap == OVERLAP_PARALLAX)
                    ? ((inv_spd << 8) | fade_u8)
                    : (((is_hd ? 1u : 0u) << 20) | (inv_spd << 8) | fade_u8);
            } else if (overlap == OVERLAP_PARALLAX) {
                uint32_t prio = (inv_spd << 8) | fade_u8;
                if (prio >= row_prio[r]) {
                    row_flags[r] = fl;
                    row_cp[r]    = disp_cp;
                    row_cid[r]   = cid;
                    row_fade[r]  = fade_u8;
                    row_wh[r]    = wh_u8;
                    row_mir[r]   = mir;
                    row_prio[r]  = prio;
                }
            } else if (overlap == OVERLAP_ADDITIVE) {
                uint32_t f0 = row_fade[r];
                uint32_t f1 = fade_u8;
                uint32_t scr = f0 + f1 - ((f0 * f1 + 127u) >> 8);
                uint32_t boost = (f0 * f1) >> 9;
                uint32_t comb = (scr + boost > 255u) ? 255u : (scr + boost);
                uint32_t c_wh = (f0 * f1) >> 9;
                if (f1 >= f0 || is_hd) {
                    row_flags[r] = fl;
                    row_cp[r]    = disp_cp;
                    row_cid[r]   = cid;
                    row_mir[r]   = mir;
                    uint32_t wsum = (uint32_t)wh_u8 + c_wh;
                    row_wh[r]    = (uint8_t)(wsum > 255u ? 255u : wsum);
                } else {
                    uint32_t wsum = (uint32_t)row_wh[r] + c_wh;
                    row_wh[r]    = (uint8_t)(wsum > 255u ? 255u : wsum);
                }
                row_fade[r] = (uint8_t)comb;
            } else {
                /* OVERLAP_HYBRID */
                uint32_t f0 = row_fade[r];
                uint32_t f1 = fade_u8;
                uint32_t scr = f0 + f1 - ((f0 * f1 + 127u) >> 8);
                uint32_t prio = ((is_hd ? 1u : 0u) << 20) | (inv_spd << 8) | fade_u8;
                if (prio >= row_prio[r]) {
                    row_flags[r] = fl;
                    row_cp[r]    = disp_cp;
                    row_cid[r]   = cid;
                    row_wh[r]    = wh_u8;
                    row_mir[r]   = mir;
                    row_prio[r]  = prio;
                }
                row_fade[r] = (uint8_t)(scr > 255u ? 255u : scr);
            }
        }
    }
}

/* Pack multi-trail simulation + optional -M message into column-major cols * lines * 4 RGBA bytes */
static void sync_grid_rgba(WallpaperApp *app) {
    MatrixSim *s = &app->sim;
    const Config *cfg = &app->cfg;
    const GlyphAtlas *a = &app->atlas;

    int total = s->cols * s->lines;
    bool full_sync = false;
    if (total > app->grid_cap) {
        free(app->grid_rgba);
        app->grid_rgba = calloc((size_t)total * 4u, 1);
        app->grid_cap = total;
        full_sync = true;
        app->dirty_col_min = 0;
        app->dirty_col_max = s->cols - 1;
    }

    const int lines = s->lines;
    const int cols = s->cols;
    const int col_step = s->col_step;
    uint32_t *dst32 = (uint32_t *)(void *)app->grid_rgba;
    int dmin = full_sync ? 0 : app->dirty_col_min;
    int dmax = full_sync ? (cols - 1) : app->dirty_col_max;
    if (dmin > cols) dmin = cols;

    for (int k = 0; k < s->n_active; k++) {
        if (!full_sync && !cfg->msg[0] && !s->col_dirty[k])
            continue;
        s->col_dirty[k] = 0;

        int j = k * col_step;
        if (j >= cols) continue;
        if (j < dmin) dmin = j;
        if (j > dmax) dmax = j;

        uint16_t row_cp[MAX_SIM_ROWS];
        uint8_t  row_cid[MAX_SIM_ROWS];
        uint8_t  row_fade[MAX_SIM_ROWS];
        uint8_t  row_wh[MAX_SIM_ROWS];
        uint8_t  row_mir[MAX_SIM_ROWS];
        uint8_t  row_flags[MAX_SIM_ROWS];
        composite_column(s, cfg, k, lines, false, row_cp, row_cid, row_fade, row_wh, row_mir, row_flags);

        size_t col_base = (size_t)j * (size_t)lines;
        uint32_t *col_dst32 = dst32 + col_base;
        for (int r = 0; r < lines; r++) {
            if (!(row_flags[r] & 1u)) {
                col_dst32[r] = 0u;
                continue;
            }
            uint16_t cp = row_cp[r];
            uint16_t base_slot = atlas_lookup_slot(a, cp);
            if (base_slot == 0) {
                base_slot = atlas_lookup_slot(a, '?');
                if (base_slot == 0 && a->n_glyphs > 0) base_slot = 1;
            }
            if (base_slot == 0) {
                col_dst32[r] = 0u;
                continue;
            }
            uint8_t st = (row_cid[r] == 255u)
                ? 1u
                : (uint8_t)(((row_flags[r] & 2u) ? 65 : 2) + row_cid[r]);
            bool bold_slot = (st == 1u) ? (cfg->bold != -1) : (st >= 65);
            uint16_t slot = (uint16_t)(base_slot + (bold_slot ? a->n_glyphs : 0));
            uint8_t wh4 = (uint8_t)(((uint32_t)row_wh[r] * 15u + 127u) / 255u);
            uint32_t packed = (uint32_t)(slot & 0xFFu)
                            | ((uint32_t)st << 8)
                            | ((uint32_t)(((slot >> 8) & 0x03u) | (row_mir[r] ? 0x04u : 0u) | (wh4 << 4)) << 16)
                            | ((uint32_t)row_fade[r] << 24);
            col_dst32[r] = packed;
        }
    }

    if (cfg->msg[0]) {
        uint8_t *dst = app->grid_rgba;
        int mlen = (int)strlen(cfg->msg);
        int mr = s->lines / 2;
        int mc = s->cols / 2 - mlen / 2;
        uint8_t mstyle = (uint8_t)(65 + (cfg->mcolor % PALETTE_COLORS));
        for (int dc = -2; dc < mlen + 2; dc++) {
            int cc = mc + dc;
            if (cc < 0 || cc >= s->cols) continue;
            if (cc < dmin) dmin = cc;
            if (cc > dmax) dmax = cc;
            for (int dr = -1; dr <= 1; dr++) {
                int rr = mr + dr;
                if (rr < 0 || rr >= s->lines) continue;
                size_t out_idx = ((size_t)cc * (size_t)s->lines + (size_t)rr) * 4u;
                if (dr == 0 && dc >= 0 && dc < mlen) {
                    uint8_t ch = (uint8_t)cfg->msg[dc];
                    uint16_t base_slot = atlas_lookup_slot(a, ch);
                    if (ch == ' ' || base_slot == 0) {
                        dst[out_idx + 0] = dst[out_idx + 1] = dst[out_idx + 2] = dst[out_idx + 3] = 0;
                    } else {
                        uint16_t slot = (uint16_t)(base_slot + a->n_glyphs);
                        dst[out_idx + 0] = (uint8_t)(slot & 0xFFu);
                        dst[out_idx + 1] = mstyle;
                        dst[out_idx + 2] = (uint8_t)((slot >> 8) & 0x03u);
                        dst[out_idx + 3] = 255u;
                    }
                } else {
                    dst[out_idx + 0] = dst[out_idx + 1] = dst[out_idx + 2] = dst[out_idx + 3] = 0;
                }
            }
        }
    }

    app->dirty_col_min = dmin;
    app->dirty_col_max = dmax;
}

/* Software incremental dirty-cell renderer (used for --software / GL fallback / software bench) */
static void sync_sw_surface(WallpaperApp *app, bool queue_dirty_regions) {
    const MatrixSim *s = &app->sim;
    const GlyphAtlas *a = &app->atlas;
    if (!app->sw_surf || !app->grid_rgba || !a->atlas_rg) return;

    int surf_w = cairo_image_surface_get_width(app->sw_surf);
    int surf_h = cairo_image_surface_get_height(app->sw_surf);
    int sw_stride = cairo_image_surface_get_stride(app->sw_surf) / 4;
    uint32_t *pixels = (uint32_t *)cairo_image_surface_get_data(app->sw_surf);

    int total = s->cols * s->lines;
    if (total > app->prev_cap) {
        free(app->prev_packed);
        app->prev_packed = malloc((size_t)total * sizeof(uint32_t));
        memset(app->prev_packed, 0xFF, (size_t)total * sizeof(uint32_t));
        app->prev_cap = total;
    }

    const int cw = a->cell_w;
    const int ch = a->cell_h;
    const int aw = a->atlas_w;
    const int c_step = app->cfg.msg[0] ? 1 : s->col_step;
    const uint32_t glow_u8 = (uint32_t)lroundf(fmaxf(0.0f, fminf(1.0f, app->cfg.glow)) * 255.0f);
    const uint32_t *grid32 = (const uint32_t *)(const void *)app->grid_rgba;
    bool any_dirty = false;
    int min_x = surf_w, min_y = surf_h, max_x = 0, max_y = 0;

    for (int c = 0; c < s->cols; c += c_step) {
        int px0 = c * cw;
        if (px0 >= surf_w) break;
        int draw_w = (px0 + cw <= surf_w) ? cw : (surf_w - px0);
        int col_base = c * s->lines;

        for (int r = 0; r < s->lines; r++) {
            int idx = col_base + r;
            uint32_t packed = grid32[idx];
            if (packed == app->prev_packed[idx] && !app->glow_dirty)
                continue;
            app->prev_packed[idx] = packed;

            int py0 = r * ch;
            if (py0 >= surf_h) break;
            int draw_h = (py0 + ch <= surf_h) ? ch : (surf_h - py0);

            const uint8_t *g = &app->grid_rgba[(size_t)idx * 4u];
            uint16_t slot = (uint16_t)g[0] | ((uint16_t)(g[2] & 0x03u) << 8);
            bool mirrored = (g[2] & 0x04u) != 0u;
            uint8_t style = g[1];
            uint8_t wh_mix = (uint8_t)(g[2] >> 4);
            uint32_t fade = g[3];
            if (slot == 0 || style == 0 || style >= PALETTE_SIZE) {
                for (int dy = 0; dy < draw_h; dy++) {
                    uint32_t *row_p = pixels + (size_t)(py0 + dy) * (size_t)sw_stride + (size_t)px0;
                    for (int dx = 0; dx < draw_w; dx++)
                        row_p[dx] = BG_ARGB32;
                }
            } else {
                int ax0 = (slot % ATLAS_COLS) * cw;
                int ay0 = (slot / ATLAS_COLS) * ch;
                const uint32_t *lut_row = app->lut[style];
                if (wh_mix == 0 && glow_u8 == 0 && !mirrored) {
                    for (int dy = 0; dy < draw_h; dy++) {
                        const uint8_t *src_rg = a->atlas_rg + ((size_t)(ay0 + dy) * (size_t)aw + (size_t)ax0) * 2u;
                        uint32_t *row_p = pixels + (size_t)(py0 + dy) * (size_t)sw_stride + (size_t)px0;
                        for (int dx = 0; dx < draw_w; dx++)
                            row_p[dx] = lut_row[(src_rg[dx * 2] * fade) >> 8];
                    }
                } else {
                    const uint32_t *white_lut = app->lut[1];
                    const uint32_t w_w = wh_mix;
                    const uint32_t c_w = 15u - wh_mix;
                    for (int dy = 0; dy < draw_h; dy++) {
                        const uint8_t *src_rg = a->atlas_rg + ((size_t)(ay0 + dy) * (size_t)aw + (size_t)ax0) * 2u;
                        uint32_t *row_p = pixels + (size_t)(py0 + dy) * (size_t)sw_stride + (size_t)px0;
                        for (int dx = 0; dx < draw_w; dx++) {
                            int sx = mirrored ? (cw - 1 - dx) : dx;
                            uint32_t sharp_a = (src_rg[sx * 2 + 0] * fade) >> 8;
                            uint32_t halo_a  = (((uint32_t)src_rg[sx * 2 + 1] * fade) >> 8) * glow_u8 >> 8;
                            uint32_t ai = sharp_a + ((halo_a * (140u + (fade >> 1))) >> 8);
                            if (ai > 255u) ai = 255u;
                            if (w_w == 0) {
                                row_p[dx] = lut_row[ai];
                            } else {
                                uint32_t c0 = lut_row[ai];
                                uint32_t c1 = white_lut[ai];
                                uint32_t rr = (((c0 >> 16) & 0xFFu) * c_w + ((c1 >> 16) & 0xFFu) * w_w + 7u) / 15u;
                                uint32_t gg = (((c0 >>  8) & 0xFFu) * c_w + ((c1 >>  8) & 0xFFu) * w_w + 7u) / 15u;
                                uint32_t bb = (( c0        & 0xFFu) * c_w + ( c1        & 0xFFu) * w_w + 7u) / 15u;
                                row_p[dx] = 0xFF000000u | (rr << 16) | (gg << 8) | bb;
                            }
                        }
                    }
                }
            }

            any_dirty = true;
            if (px0 < min_x) min_x = px0;
            if (py0 < min_y) min_y = py0;
            if (px0 + draw_w > max_x) max_x = px0 + draw_w;
            if (py0 + draw_h > max_y) max_y = py0 + draw_h;
        }
    }
    app->glow_dirty = false;
    cairo_surface_mark_dirty(app->sw_surf);
    if (queue_dirty_regions && app->sw_area && any_dirty) {
        int sx = min_x / a->scale;
        int sy = min_y / a->scale;
        int sw = (max_x - min_x + a->scale - 1) / a->scale + 1;
        int sh = (max_y - min_y + a->scale - 1) / a->scale + 1;
        gtk_widget_queue_draw_area(app->sw_area, sx, sy, sw, sh);
    }
}

/* ============================================================================
 * OpenGL ES 3.0 / OpenGL 3.2+ Shader Renderer
 * ============================================================================ */

static GLuint compile_shader(GLenum type, const char *hdr, const char *body) {
    GLuint sh = glCreateShader(type);
    const char *srcs[2] = {hdr, body};
    glShaderSource(sh, 2, srcs, NULL);
    glCompileShader(sh);
    GLint ok = 0;
    glGetShaderiv(sh, GL_COMPILE_STATUS, &ok);
    if (!ok) {
        char log[512];
        glGetShaderInfoLog(sh, sizeof(log), NULL, log);
        fprintf(stderr, "nc-matrix GL shader error: %s\n", log);
        glDeleteShader(sh);
        return 0;
    }
    return sh;
}

static void resize_grid_for_pixels(WallpaperApp *app, int phys_w, int phys_h, int scale, bool force_reinit) {
    if (phys_w < 16) phys_w = 16;
    if (phys_h < 16) phys_h = 16;
    if (scale < 1) scale = 1;

    int req_fs = app->cfg.font_size;
    if (req_fs < 5) req_fs = 5;
    if (req_fs > 32) req_fs = 32;

    if (app->atlas.atlas_rg == NULL || app->atlas.scale != scale || app->atlas.font_size != req_fs) {
        atlas_build(&app->atlas, &app->cfg, scale);
        app->prev_cap = 0;
        free(app->prev_packed);
        app->prev_packed = NULL;
    }

    int cols = phys_w / app->atlas.cell_w;
    int rows = phys_h / app->atlas.cell_h;
    if (cols < 4) cols = 4;
    if (rows < 4) rows = 4;
    if (rows > MAX_SIM_ROWS) rows = MAX_SIM_ROWS;
    int want_step = app->cfg.dense_cols ? 1 : 2;

    if (force_reinit || cols != app->sim.cols || rows != app->sim.lines || want_step != app->sim.col_step) {
        sim_init(&app->sim, rows, cols, &app->cfg);
        if (force_reinit && app->cfg.spawn_rate > 0.001f) {
            int base_ms = sim_base_step_ms(&app->cfg);
            uint32_t max_sp = (uint32_t)sim_max_space(rows, app->cfg.spawn_rate);
            uint32_t span = (max_sp / 4u > 1u) ? (max_sp / 4u) : 2u;
            for (int k = 0; k < app->sim.n_active; k++)
                app->sim.spawn_cd_ms[k] = (int32_t)((rng_u32(&app->sim.rngs[k], span) + 1u) * (uint32_t)base_ms);
        }
        if (app->grid_rgba && app->grid_cap > 0)
            memset(app->grid_rgba, 0, (size_t)app->grid_cap * 4u);
        app->dirty_col_min = 0;
        app->dirty_col_max = cols - 1;
        app->gl_state_dirty = true;
        sync_grid_rgba(app);
        app->prev_cap = 0;
        free(app->prev_packed);
        app->prev_packed = NULL;
    }
}

static void upload_atlas_tex(WallpaperApp *app) {
    if (!app->atlas.atlas_rg) return;
    glBindTexture(GL_TEXTURE_2D, app->tex_atlas);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RG8, app->atlas.atlas_w, app->atlas.atlas_h, 0,
                 GL_RG, GL_UNSIGNED_BYTE, app->atlas.atlas_rg);
}

static void upload_grid_alloc_tex(WallpaperApp *app) {
    glBindTexture(GL_TEXTURE_2D, app->tex_grid);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8UI, app->sim.lines, app->sim.cols, 0,
                 GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, app->grid_rgba);
    app->dirty_col_min = app->sim.cols;
    app->dirty_col_max = -1;
    app->gl_state_dirty = true;
}

#ifndef EGL_PLATFORM_WAYLAND_KHR
#define EGL_PLATFORM_WAYLAND_KHR 0x31D8
#endif

static void registry_global(void *data, struct wl_registry *reg, uint32_t name,
                            const char *iface, uint32_t ver) {
    WallpaperApp *app = (WallpaperApp *)data;
    if (strcmp(iface, wl_compositor_interface.name) == 0 && !app->wl_comp) {
        uint32_t bind_ver = (ver >= 4u) ? 4u : ver;
        app->wl_comp = wl_registry_bind(reg, name, &wl_compositor_interface, bind_ver);
    } else if (strcmp(iface, wl_subcompositor_interface.name) == 0 && !app->wl_subcomp) {
        app->wl_subcomp = wl_registry_bind(reg, name, &wl_subcompositor_interface, 1u);
    }
}

static void registry_remove(void *data, struct wl_registry *reg, uint32_t name) {
    (void)data;
    (void)reg;
    (void)name;
}

static const struct wl_registry_listener k_wl_reg_listener = {
    .global = registry_global,
    .global_remove = registry_remove,
};

static void update_wl_regions(WallpaperApp *app, int log_w, int log_h) {
    if (!app->wl_comp || !app->wl_surf) return;
    struct wl_region *inp = wl_compositor_create_region(app->wl_comp);
    if (inp) {
        wl_surface_set_input_region(app->wl_surf, inp);
        wl_region_destroy(inp);
    }
    struct wl_region *opq = wl_compositor_create_region(app->wl_comp);
    if (opq) {
        wl_region_add(opq, 0, 0, log_w, log_h);
        wl_surface_set_opaque_region(app->wl_surf, opq);
        wl_region_destroy(opq);
    }
}

static void cleanup_wayland_egl(WallpaperApp *app) {
    if (app->egl_dpy != EGL_NO_DISPLAY) {
        if (app->egl_ctx != EGL_NO_CONTEXT && app->egl_surf != EGL_NO_SURFACE) {
            eglMakeCurrent(app->egl_dpy, app->egl_surf, app->egl_surf, app->egl_ctx);
            if (app->tex_atlas) { glDeleteTextures(1, &app->tex_atlas); app->tex_atlas = 0; }
            if (app->tex_grid)  { glDeleteTextures(1, &app->tex_grid);  app->tex_grid = 0; }
            if (app->vao)       { glDeleteVertexArrays(1, &app->vao);   app->vao = 0; }
            if (app->prog)      { glDeleteProgram(app->prog);           app->prog = 0; }
        }
        eglMakeCurrent(app->egl_dpy, EGL_NO_SURFACE, EGL_NO_SURFACE, EGL_NO_CONTEXT);
        if (app->egl_surf != EGL_NO_SURFACE) {
            eglDestroySurface(app->egl_dpy, app->egl_surf);
            app->egl_surf = EGL_NO_SURFACE;
        }
        if (app->egl_ctx != EGL_NO_CONTEXT) {
            eglDestroyContext(app->egl_dpy, app->egl_ctx);
            app->egl_ctx = EGL_NO_CONTEXT;
        }
        app->egl_dpy = EGL_NO_DISPLAY;
    }
    if (app->egl_win)    { wl_egl_window_destroy(app->egl_win);       app->egl_win = NULL; }
    if (app->wl_sub)     { wl_subsurface_destroy(app->wl_sub);        app->wl_sub = NULL; }
    if (app->wl_surf)    { wl_surface_destroy(app->wl_surf);          app->wl_surf = NULL; }
    if (app->wl_subcomp) { wl_subcompositor_destroy(app->wl_subcomp); app->wl_subcomp = NULL; }
    if (app->wl_comp)    { wl_compositor_destroy(app->wl_comp);       app->wl_comp = NULL; }
    app->wl_parent_surf = NULL;
    app->wl_dpy = NULL;
    app->gl_ready = false;
}

static void switch_to_software_fallback(WallpaperApp *app, const char *reason) {
    fprintf(stderr, "nc-matrix: %s, falling back to software renderer\n",
            reason ? reason : "EGL init failed");
    cleanup_wayland_egl(app);
    app->gl_failed = true;
}

static bool choose_egl_cfg_and_ctx(WallpaperApp *app, bool try_es, EGLConfig *out_cfg) {
    for (int alpha_pass = 0; alpha_pass < 2; alpha_pass++) {
        EGLint cfg_attrs[16];
        int n = 0;
        cfg_attrs[n++] = EGL_SURFACE_TYPE;    cfg_attrs[n++] = EGL_WINDOW_BIT;
        cfg_attrs[n++] = EGL_RENDERABLE_TYPE; cfg_attrs[n++] = try_es ? EGL_OPENGL_ES3_BIT : EGL_OPENGL_BIT;
        cfg_attrs[n++] = EGL_RED_SIZE;        cfg_attrs[n++] = 8;
        cfg_attrs[n++] = EGL_GREEN_SIZE;      cfg_attrs[n++] = 8;
        cfg_attrs[n++] = EGL_BLUE_SIZE;       cfg_attrs[n++] = 8;
        if (alpha_pass == 0) {
            cfg_attrs[n++] = EGL_ALPHA_SIZE;  cfg_attrs[n++] = 0;
        }
        cfg_attrs[n++] = EGL_NONE;

        EGLint num_cfg = 0;
        if (!eglChooseConfig(app->egl_dpy, cfg_attrs, out_cfg, 1, &num_cfg) || num_cfg < 1)
            continue;

        if (!eglBindAPI(try_es ? EGL_OPENGL_ES_API : EGL_OPENGL_API))
            return false;

        if (try_es) {
            static const EGLint ctx_es[] = {
                EGL_CONTEXT_CLIENT_VERSION, 3,
                EGL_NONE
            };
            app->egl_ctx = eglCreateContext(app->egl_dpy, *out_cfg, EGL_NO_CONTEXT, ctx_es);
        } else {
            static const EGLint ctx_gl[] = {
                EGL_CONTEXT_MAJOR_VERSION, 3,
                EGL_CONTEXT_MINOR_VERSION, 2,
                EGL_CONTEXT_OPENGL_PROFILE_MASK, EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,
                EGL_NONE
            };
            app->egl_ctx = eglCreateContext(app->egl_dpy, *out_cfg, EGL_NO_CONTEXT, ctx_gl);
        }
        if (app->egl_ctx != EGL_NO_CONTEXT) {
            app->egl_use_es = try_es;
            return true;
        }
    }
    return false;
}

static void draw_gl_current_frame(WallpaperApp *app) {
    if (app->gl_state_dirty) {
        glViewport(0, 0, app->egl_w, app->egl_h);
        glUseProgram(app->prog);
        glBindVertexArray(app->vao);
        glActiveTexture(GL_TEXTURE0);
        glBindTexture(GL_TEXTURE_2D, app->tex_atlas);
        glActiveTexture(GL_TEXTURE1);
        glBindTexture(GL_TEXTURE_2D, app->tex_grid);
        glPixelStorei(GL_UNPACK_ALIGNMENT, 4);
        glUniform2i(app->u_view_size, app->egl_w, app->egl_h);
        glUniform2i(app->u_cell_size, app->atlas.cell_w, app->atlas.cell_h);
        glUniform2i(app->u_grid_size, app->sim.cols, app->sim.lines);
        glUniform1f(app->u_glow, fmaxf(0.0f, fminf(1.0f, app->cfg.glow)));
        app->gl_state_dirty = false;
    }

    if (app->dirty_col_min <= app->dirty_col_max) {
        int c0 = app->dirty_col_min < 0 ? 0 : app->dirty_col_min;
        int c1 = app->dirty_col_max >= app->sim.cols ? (app->sim.cols - 1) : app->dirty_col_max;
        if (c0 <= c1) {
            glTexSubImage2D(GL_TEXTURE_2D, 0, 0, c0, app->sim.lines, c1 - c0 + 1,
                            GL_RGBA_INTEGER, GL_UNSIGNED_BYTE,
                            app->grid_rgba + (size_t)c0 * (size_t)app->sim.lines * 4u);
        }
        app->dirty_col_min = app->sim.cols;
        app->dirty_col_max = -1;
    }

    glDrawArrays(GL_TRIANGLES, 0, 3);
    eglSwapBuffers(app->egl_dpy, app->egl_surf);
}

static bool init_wayland_egl(WallpaperApp *app, int log_w, int log_h, int phys_w, int phys_h, int scale) {
    if (!app->win || !app->sw_area) return false;
    GdkDisplay *gdk_dpy = gtk_widget_get_display(app->win);
    GdkWindow *gdk_win = gtk_widget_get_window(app->win);
    if (!gdk_dpy || !gdk_win || !GDK_IS_WAYLAND_DISPLAY(gdk_dpy) || !GDK_IS_WAYLAND_WINDOW(gdk_win)) {
        switch_to_software_fallback(app, "non-Wayland GDK display/window");
        return false;
    }

    app->wl_dpy = gdk_wayland_display_get_wl_display(gdk_dpy);
    app->wl_parent_surf = gdk_wayland_window_get_wl_surface(gdk_win);
    if (!app->wl_dpy || !app->wl_parent_surf) {
        switch_to_software_fallback(app, "missing wl_display or parent wl_surface");
        return false;
    }

    struct wl_registry *reg = wl_display_get_registry(app->wl_dpy);
    if (!reg) {
        switch_to_software_fallback(app, "wl_display_get_registry failed");
        return false;
    }
    wl_registry_add_listener(reg, &k_wl_reg_listener, app);
    wl_display_roundtrip(app->wl_dpy);
    wl_registry_destroy(reg);

    if (!app->wl_comp || !app->wl_subcomp) {
        switch_to_software_fallback(app, "Wayland compositor/subcompositor missing");
        return false;
    }

    app->wl_surf = wl_compositor_create_surface(app->wl_comp);
    if (!app->wl_surf) {
        switch_to_software_fallback(app, "wl_compositor_create_surface failed");
        return false;
    }
    update_wl_regions(app, log_w, log_h);
    if (scale > 1)
        wl_surface_set_buffer_scale(app->wl_surf, scale);

    app->wl_sub = wl_subcompositor_get_subsurface(app->wl_subcomp, app->wl_surf, app->wl_parent_surf);
    if (!app->wl_sub) {
        switch_to_software_fallback(app, "wl_subcompositor_get_subsurface failed");
        return false;
    }
    wl_subsurface_set_desync(app->wl_sub);
    wl_subsurface_set_position(app->wl_sub, 0, 0);
    wl_subsurface_place_above(app->wl_sub, app->wl_parent_surf);

    app->egl_w = phys_w;
    app->egl_h = phys_h;
    app->egl_win = wl_egl_window_create(app->wl_surf, phys_w, phys_h);
    if (!app->egl_win) {
        switch_to_software_fallback(app, "wl_egl_window_create failed");
        return false;
    }

    if (epoxy_has_egl_extension(EGL_NO_DISPLAY, "EGL_EXT_platform_base")) {
        PFNEGLGETPLATFORMDISPLAYEXTPROC get_plat =
            (PFNEGLGETPLATFORMDISPLAYEXTPROC)eglGetProcAddress("eglGetPlatformDisplayEXT");
        if (get_plat)
            app->egl_dpy = get_plat(EGL_PLATFORM_WAYLAND_KHR, app->wl_dpy, NULL);
    }
    if (app->egl_dpy == EGL_NO_DISPLAY)
        app->egl_dpy = eglGetDisplay((EGLNativeDisplayType)(void *)app->wl_dpy);

    if (app->egl_dpy == EGL_NO_DISPLAY || !eglInitialize(app->egl_dpy, NULL, NULL)) {
        switch_to_software_fallback(app, "eglInitialize failed");
        return false;
    }

    EGLConfig egl_cfg = NULL;
    if (!choose_egl_cfg_and_ctx(app, true, &egl_cfg) &&
        !choose_egl_cfg_and_ctx(app, false, &egl_cfg)) {
        switch_to_software_fallback(app, "no compatible EGL ES 3.0 / GL 3.2 config");
        return false;
    }

    app->egl_surf = eglCreateWindowSurface(app->egl_dpy, egl_cfg,
                                           (EGLNativeWindowType)(uintptr_t)app->egl_win, NULL);
    if (app->egl_surf == EGL_NO_SURFACE ||
        !eglMakeCurrent(app->egl_dpy, app->egl_surf, app->egl_surf, app->egl_ctx)) {
        switch_to_software_fallback(app, "eglCreateWindowSurface/eglMakeCurrent failed");
        return false;
    }
    eglSwapInterval(app->egl_dpy, 0);

    const char *hdr = app->egl_use_es
        ? "#version 300 es\nprecision highp float;\nprecision highp int;\nprecision highp usampler2D;\n"
        : "#version 150\n";

    static const char *vs_body =
        "void main() {\n"
        "    float x = -1.0 + float((gl_VertexID & 1) << 2);\n"
        "    float y = -1.0 + float((gl_VertexID & 2) << 1);\n"
        "    gl_Position = vec4(x, y, 0.0, 1.0);\n"
        "}\n";

    static const char *fs_body =
        "uniform sampler2D u_atlas;\n"
        "uniform usampler2D u_grid;\n"
        "uniform ivec2 u_view_size;\n"
        "uniform ivec2 u_cell_size;\n"
        "uniform ivec2 u_grid_size;\n"
        "uniform vec3 u_palette[128];\n"
        "uniform vec3 u_bg;\n"
        "uniform float u_glow;\n"
        "out vec4 fragColor;\n"
        "void main() {\n"
        "    int px = int(gl_FragCoord.x);\n"
        "    int py = u_view_size.y - 1 - int(gl_FragCoord.y);\n"
        "    int col = px / u_cell_size.x;\n"
        "    int row = py / u_cell_size.y;\n"
        "    if (col < 0 || row < 0 || col >= u_grid_size.x || row >= u_grid_size.y) {\n"
        "        fragColor = vec4(u_bg, 1.0);\n"
        "        return;\n"
        "    }\n"
        "    uvec4 cell = texelFetch(u_grid, ivec2(row, col), 0);\n"
        "    uint slot = cell.r + ((cell.b & 3u) << 8u);\n"
        "    uint style = cell.g;\n"
        "    if (slot == 0u || style == 0u) {\n"
        "        fragColor = vec4(u_bg, 1.0);\n"
        "        return;\n"
        "    }\n"
        "    uint wh_mix = cell.b >> 4u;\n"
        "    int lx = px - col * u_cell_size.x;\n"
        "    if ((cell.b & 4u) != 0u) lx = u_cell_size.x - 1 - lx;\n"
        "    int ly = py - row * u_cell_size.y;\n"
        "    int acol = int(slot) & 15;\n"
        "    int arow = int(slot) >> 4;\n"
        "    vec2 ag = texelFetch(u_atlas, ivec2(acol * u_cell_size.x + lx, arow * u_cell_size.y + ly), 0).rg;\n"
        "    float fade = float(cell.a) * (1.0 / 255.0);\n"
        "    float sharp = ag.r * fade;\n"
        "    float halo = ag.g * fade * u_glow;\n"
        "    float a = min(1.0, sharp + halo * (0.55 + 0.55 * fade));\n"
        "    vec3 fg = u_palette[min(int(style), 127)];\n"
        "    float bloom_wh = float(wh_mix) * (1.0 / 15.0) + halo * sharp * 0.28;\n"
        "    if (bloom_wh > 0.0) fg = mix(fg, u_palette[1], min(1.0, bloom_wh));\n"
        "    fragColor = vec4(mix(u_bg, fg, a), 1.0);\n"
        "}\n";

    GLuint vs = compile_shader(GL_VERTEX_SHADER, hdr, vs_body);
    GLuint fs = compile_shader(GL_FRAGMENT_SHADER, hdr, fs_body);
    if (!vs || !fs) {
        if (vs) glDeleteShader(vs);
        if (fs) glDeleteShader(fs);
        switch_to_software_fallback(app, "GL shader compile failed");
        return false;
    }

    GLuint prog = glCreateProgram();
    glAttachShader(prog, vs);
    glAttachShader(prog, fs);
    glLinkProgram(prog);
    glDeleteShader(vs);
    glDeleteShader(fs);

    GLint linked = 0;
    glGetProgramiv(prog, GL_LINK_STATUS, &linked);
    if (!linked) {
        glDeleteProgram(prog);
        switch_to_software_fallback(app, "GL program link failed");
        return false;
    }

    app->prog = prog;
    app->u_view_size = glGetUniformLocation(prog, "u_view_size");
    app->u_cell_size = glGetUniformLocation(prog, "u_cell_size");
    app->u_grid_size = glGetUniformLocation(prog, "u_grid_size");
    app->u_palette   = glGetUniformLocation(prog, "u_palette");
    app->u_bg        = glGetUniformLocation(prog, "u_bg");
    app->u_glow      = glGetUniformLocation(prog, "u_glow");

    glUseProgram(prog);
    glUniform1i(glGetUniformLocation(prog, "u_atlas"), 0);
    glUniform1i(glGetUniformLocation(prog, "u_grid"), 1);
    glUniform3fv(app->u_palette, PALETTE_SIZE, &app->palette_rgb[0][0]);
    app->palette_dirty = false;
    app->glow_dirty = false;
    glUniform3f(app->u_bg, BG_R, BG_G, BG_B);
    glUniform1f(app->u_glow, fmaxf(0.0f, fminf(1.0f, app->cfg.glow)));

    glGenVertexArrays(1, &app->vao);
    glBindVertexArray(app->vao);
    glGenTextures(1, &app->tex_atlas);
    glGenTextures(1, &app->tex_grid);

    resize_grid_for_pixels(app, phys_w, phys_h, scale, false);
    upload_atlas_tex(app);
    upload_grid_alloc_tex(app);
    app->gl_ready = true;
    app->gl_state_dirty = true;

    draw_gl_current_frame(app);
    wl_surface_commit(app->wl_parent_surf);
    wl_display_flush(app->wl_dpy);
    return true;
}

static void render_egl_frame(WallpaperApp *app) {
    if (!app->gl_ready || !app->sw_area) return;

    GtkAllocation alloc;
    gtk_widget_get_allocation(app->sw_area, &alloc);
    int scale = gtk_widget_get_scale_factor(app->sw_area);
    if (scale < 1) scale = 1;
    int pw = MAX(16, alloc.width * scale);
    int ph = MAX(16, alloc.height * scale);

    if (app->palette_dirty) {
        glUniform3fv(app->u_palette, PALETTE_SIZE, &app->palette_rgb[0][0]);
        app->palette_dirty = false;
    }

    if (pw != app->egl_w || ph != app->egl_h || scale != app->atlas.scale || app->cfg.font_size != app->atlas.font_size) {
        wl_surface_set_buffer_scale(app->wl_surf, scale);
        wl_egl_window_resize(app->egl_win, pw, ph, 0, 0);
        update_wl_regions(app, alloc.width, alloc.height);
        app->egl_w = pw;
        app->egl_h = ph;

        int prev_cols = app->sim.cols;
        int prev_rows = app->sim.lines;
        int prev_scale = app->atlas.scale;
        int prev_fs = app->atlas.font_size;
        resize_grid_for_pixels(app, pw, ph, scale, false);
        if (app->atlas.scale != prev_scale || app->atlas.font_size != prev_fs)
            upload_atlas_tex(app);
        if (app->sim.cols != prev_cols || app->sim.lines != prev_rows)
            upload_grid_alloc_tex(app);
        app->gl_state_dirty = true;
    }

    draw_gl_current_frame(app);
    wl_display_flush(app->wl_dpy);
}

static gboolean on_sw_draw(GtkWidget *widget, cairo_t *cr, gpointer user_data) {
    WallpaperApp *app = (WallpaperApp *)user_data;
    GtkAllocation alloc;
    gtk_widget_get_allocation(widget, &alloc);
    int scale = gtk_widget_get_scale_factor(widget);
    if (scale < 1) scale = 1;
    int pw = MAX(16, alloc.width * scale);
    int ph = MAX(16, alloc.height * scale);

    if (!app->cfg.software && !app->gl_failed) {
        if (!app->gl_ready)
            init_wayland_egl(app, MAX(16, alloc.width), MAX(16, alloc.height), pw, ph, scale);
        if (app->gl_ready) {
            cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
            cairo_set_source_rgb(cr, BG_R, BG_G, BG_B);
            cairo_paint(cr);
            return FALSE;
        }
    }

    if (!app->sw_surf ||
        cairo_image_surface_get_width(app->sw_surf) != pw ||
        cairo_image_surface_get_height(app->sw_surf) != ph ||
        app->atlas.scale != scale ||
        app->atlas.font_size != app->cfg.font_size) {
        if (app->sw_surf) cairo_surface_destroy(app->sw_surf);
        app->sw_surf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, pw, ph);
        uint32_t *px = (uint32_t *)cairo_image_surface_get_data(app->sw_surf);
        int st = cairo_image_surface_get_stride(app->sw_surf) / 4;
        for (int y = 0; y < ph; y++)
            for (int x = 0; x < pw; x++)
                px[(size_t)y * (size_t)st + (size_t)x] = BG_ARGB32;
        resize_grid_for_pixels(app, pw, ph, scale, false);
        sync_sw_surface(app, false);
    }

    cairo_save(cr);
    if (scale > 1)
        cairo_scale(cr, 1.0 / (double)scale, 1.0 / (double)scale);
    cairo_set_operator(cr, CAIRO_OPERATOR_SOURCE);
    cairo_set_source_surface(cr, app->sw_surf, 0, 0);
    cairo_paint(cr);
    cairo_restore(cr);
    return FALSE;
}

static gboolean on_wallpaper_tick(gpointer user_data);

static void wp_reschedule_timer(WallpaperApp *app, int interval_ms) {
    if (interval_ms < WP_BASE_TICK_MS) interval_ms = WP_BASE_TICK_MS;
    if (app->timer_id && app->timer_interval_ms == interval_ms)
        return;
    if (app->timer_id) {
        g_source_remove(app->timer_id);
        app->timer_id = 0;
    }
    app->timer_interval_ms = interval_ms;
    if (app->running)
        app->timer_id = g_timeout_add((guint)interval_ms, on_wallpaper_tick, app);
}

static void wp_reinit_after_drain(WallpaperApp *app) {
    int pw = MAX(16, app->egl_w);
    int ph = MAX(16, app->egl_h);
    int scale = 1;
    if (app->sw_area) {
        GtkAllocation alloc;
        gtk_widget_get_allocation(app->sw_area, &alloc);
        scale = gtk_widget_get_scale_factor(app->sw_area);
        if (scale < 1) scale = 1;
        pw = MAX(16, alloc.width * scale);
        ph = MAX(16, alloc.height * scale);
    }
    int prev_cols = app->sim.cols;
    int prev_rows = app->sim.lines;
    int prev_scale = app->atlas.scale;
    int prev_fs = app->atlas.font_size;
    resize_grid_for_pixels(app, pw, ph, scale, true);

    if (!app->cfg.software && !app->gl_failed && app->gl_ready) {
        if (eglGetCurrentContext() != app->egl_ctx)
            eglMakeCurrent(app->egl_dpy, app->egl_surf, app->egl_surf, app->egl_ctx);
        if (app->atlas.scale != prev_scale || app->atlas.font_size != prev_fs)
            upload_atlas_tex(app);
        if (app->sim.cols != prev_cols || app->sim.lines != prev_rows)
            upload_grid_alloc_tex(app);
    } else if (app->sw_surf) {
        uint32_t *px = (uint32_t *)cairo_image_surface_get_data(app->sw_surf);
        int sh = cairo_image_surface_get_height(app->sw_surf);
        int sw = cairo_image_surface_get_width(app->sw_surf);
        int st = cairo_image_surface_get_stride(app->sw_surf) / 4;
        for (int y = 0; y < sh; y++)
            for (int x = 0; x < sw; x++)
                px[(size_t)y * (size_t)st + (size_t)x] = BG_ARGB32;
        cairo_surface_mark_dirty(app->sw_surf);
    }
    wp_reschedule_timer(app, compute_optimal_timer_ms(&app->cfg, false));
}

static gboolean on_wallpaper_tick(gpointer user_data) {
    WallpaperApp *app = (WallpaperApp *)user_data;
    if (!app->running) return G_SOURCE_REMOVE;

    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    int64_t now_ns = (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
    int dt_ms = app->timer_interval_ms > 0 ? app->timer_interval_ms : 16;
    if (app->last_tick_ns > 0 && now_ns > app->last_tick_ns) {
        int64_t diff_ms = (now_ns - app->last_tick_ns + 500000LL) / 1000000LL;
        int max_dt = (app->timer_interval_ms > 125) ? (app->timer_interval_ms * 2) : 250;
        if (diff_ms < 1) diff_ms = 1;
        if (diff_ms > max_dt) diff_ms = max_dt;
        dt_ms = (int)diff_ms;
    }
    app->last_tick_ns = now_ns;

    if (app->cfg.paused) return G_SOURCE_CONTINUE;

    bool any_stepped = sim_step_dt(&app->sim, &app->cfg, dt_ms);
    int want_ms = compute_optimal_timer_ms(&app->cfg, app->sim.draining);
    if (app->timer_interval_ms != want_ms)
        wp_reschedule_timer(app, want_ms);

    if (!any_stepped && !app->palette_dirty && !app->glow_dirty && !app->sim.draining)
        return G_SOURCE_CONTINUE;

    if (any_stepped || app->glow_dirty || app->palette_dirty)
        sync_grid_rgba(app);
    if (!app->cfg.software && !app->gl_failed && app->gl_ready) {
        if (app->glow_dirty) {
            app->gl_state_dirty = true;
            app->glow_dirty = false;
        }
        render_egl_frame(app);
    } else if (app->sw_surf) {
        sync_sw_surface(app, true);
    } else if (app->sw_area) {
        gtk_widget_queue_draw(app->sw_area);
    }

    if (app->sim.draining && sim_is_screen_empty(&app->sim, &app->cfg)) {
        app->sim.draining = false;
        if (app->has_pending_cfg) {
            app->cfg = app->pending_cfg;
            app->has_pending_cfg = false;
        }
        wp_reinit_after_drain(app);
    }

    return G_SOURCE_CONTINUE;
}

/* ============================================================================
 * Wayland Env, Monitor Matching, PID & UNIX Socket IPC
 * ============================================================================ */

static void ensure_wayland_env(void) {
    char uid_buf[64];
    const char *rt = getenv("XDG_RUNTIME_DIR");
    if (!rt || !*rt) {
        snprintf(uid_buf, sizeof(uid_buf), "/run/user/%d", (int)getuid());
        setenv("XDG_RUNTIME_DIR", uid_buf, 1);
        rt = getenv("XDG_RUNTIME_DIR");
    }
    const char *wd = getenv("WAYLAND_DISPLAY");
    if ((!wd || !*wd) && rt) {
        DIR *d = opendir(rt);
        if (d) {
            struct dirent *ent;
            char best[256] = "";
            while ((ent = readdir(d)) != NULL) {
                if (strncmp(ent->d_name, "wayland-", 8) == 0 && !strstr(ent->d_name, ".lock")) {
                    if (!best[0] || strcmp(ent->d_name, best) < 0)
                        snprintf(best, sizeof(best), "%s", ent->d_name);
                }
            }
            closedir(d);
            if (best[0]) setenv("WAYLAND_DISPLAY", best, 1);
        }
    }
}

static GdkMonitor *find_target_monitor(const Config *cfg) {
    GdkDisplay *disp = gdk_display_get_default();
    if (!disp) return NULL;
    int n = gdk_display_get_n_monitors(disp);
    if (cfg->has_pos) {
        for (int i = 0; i < n; i++) {
            GdkMonitor *m = gdk_display_get_monitor(disp, i);
            if (!m) continue;
            GdkRectangle g;
            gdk_monitor_get_geometry(m, &g);
            if (g.x == cfg->req_x && g.y == cfg->req_y)
                return m;
        }
    }
    if (cfg->req_w > 0 && cfg->req_h > 0) {
        for (int i = 0; i < n; i++) {
            GdkMonitor *m = gdk_display_get_monitor(disp, i);
            if (!m) continue;
            GdkRectangle g;
            gdk_monitor_get_geometry(m, &g);
            if (g.width == cfg->req_w && g.height == cfg->req_h)
                return m;
        }
    }
    return (n > 0) ? gdk_display_get_monitor(disp, 0) : NULL;
}

static void write_pid_file(const Config *cfg) {
    if (!cfg->pid_file[0]) return;
    char dir_buf[512];
    snprintf(dir_buf, sizeof(dir_buf), "%s", cfg->pid_file);
    char *slash = strrchr(dir_buf, '/');
    if (slash) {
        *slash = '\0';
        g_mkdir_with_parents(dir_buf, 0755);
    }
    FILE *f = fopen(cfg->pid_file, "w");
    if (f) {
        fprintf(f, "%d\n", (int)getpid());
        fclose(f);
    }
}

static void cleanup_runtime_files(WallpaperApp *app) {
    if (app->cfg.pid_file[0]) {
        FILE *f = fopen(app->cfg.pid_file, "r");
        if (f) {
            int p = 0;
            if (fscanf(f, "%d", &p) == 1 && p == (int)getpid()) {
                fclose(f);
                unlink(app->cfg.pid_file);
            } else {
                fclose(f);
            }
        }
    }
    if (app->cfg.ipc_socket[0]) {
        struct stat st;
        if (stat(app->cfg.ipc_socket, &st) == 0 && st.st_ino == app->sock_ino)
            unlink(app->cfg.ipc_socket);
    }
}

static void wallpaper_shutdown(WallpaperApp *app) {
    if (!app->running) return;
    app->running = false;
    if (app->timer_id) { g_source_remove(app->timer_id); app->timer_id = 0; }
    if (app->sock_watch) { g_source_remove(app->sock_watch); app->sock_watch = 0; }
    if (app->sock_fd >= 0) { close(app->sock_fd); app->sock_fd = -1; }
    cleanup_wayland_egl(app);
    cleanup_runtime_files(app);
    if (app->win) {
        gtk_widget_destroy(app->win);
        app->win = NULL;
    }
    gtk_main_quit();
}

static void wp_reload_config(WallpaperApp *app, bool force_drain) {
    char path[256];
    snprintf(path, sizeof(path), "/tmp/nc-matrix_%s.conf",
             app->cfg.output_name[0] ? app->cfg.output_name : "default");
    FILE *f = fopen(path, "r");
    if (!f) {
        if (force_drain) {
            app->pending_cfg = app->cfg;
            app->has_pending_cfg = true;
            app->sim.draining = true;
            wp_reschedule_timer(app, 28);
        }
        return;
    }

    Config next = app->has_pending_cfg ? app->pending_cfg : app->cfg;
    bool req_drain = force_drain;
    char line[256];
    while (fgets(line, sizeof(line), f)) {
        char *eq = strchr(line, '=');
        if (!eq) continue;
        *eq = '\0';
        char *k = line;
        char *v = eq + 1;
        while (*k && isspace((unsigned char)*k)) k++;
        char *ke = k + strlen(k);
        while (ke > k && isspace((unsigned char)ke[-1])) *--ke = '\0';
        while (*v && isspace((unsigned char)*v)) v++;
        char *ve = v + strlen(v);
        while (ve > v && isspace((unsigned char)ve[-1])) *--ve = '\0';
        if (!*k || !*v) continue;

        if (!strcasecmp(k, "charset")) {
            next.charset = charset_from_name(v);
        } else if (!strcasecmp(k, "color")) {
            if (!strcasecmp(v, "rainbow")) {
                next.rainbow = 1;
                next.has_custom_rgb = false;
            } else {
                uint8_t rgb[3];
                if (parse_hex_rgb(v, rgb)) {
                    register_custom_hex_color(app, &next, rgb);
                } else {
                    int c = color_from_name(v);
                    if (c >= 0) {
                        next.mcolor = c;
                        next.rainbow = 0;
                        next.has_custom_rgb = false;
                    }
                }
            }
        } else if (!strcasecmp(k, "speed")) {
            int sp = atoi(v);
            if (sp >= 1 && sp <= 10)
                next.speed = sp;
        } else if (!strcasecmp(k, "spawn_rate")) {
            float sr = 1.0f;
            if (parse_float_clamped(v, 0.0f, 3.0f, &sr))
                next.spawn_rate = sr;
        } else if (!strcasecmp(k, "head_color")) {
            int hm = HEAD_WHITE;
            if (parse_enum_table(v, k_head_tbl, &hm))
                next.head_mode = hm;
        } else if (!strcasecmp(k, "speed_variance")) {
            float sv = 5.0f;
            if (parse_float_clamped(v, 0.0f, 10.0f, &sv)) {
                next.speed_variance = sv;
                next.has_speed_var = 1;
            }
        } else if (!strcasecmp(k, "overlap_mode") || !strcasecmp(k, "overlap")) {
            int ov = OVERLAP_HYBRID;
            if (parse_enum_table(v, k_overlap_tbl, &ov))
                next.overlap_mode = ov;
        } else if (!strcasecmp(k, "depth_parallax") || !strcasecmp(k, "parallax")) {
            float dp = 0.50f;
            if (parse_parallax_strength(v, &dp))
                next.depth_parallax = dp;
        } else if (!strcasecmp(k, "glow")) {
            float gl = 0.60f;
            if (parse_float_clamped(v, 0.0f, 1.0f, &gl))
                next.glow = gl;
        } else if (!strcasecmp(k, "decay_mode") || !strcasecmp(k, "decay")) {
            int dm = DECAY_EXP;
            if (parse_enum_table(v, k_decay_tbl, &dm))
                next.decay_mode = dm;
        } else if (!strcasecmp(k, "shimmer")) {
            int sh = SHIMMER_INSANE;
            if (parse_enum_table(v, k_shimmer_tbl, &sh)) {
                next.shimmer_mode = sh;
                next.has_shimmer = 1;
            }
        } else if (!strcasecmp(k, "mirror")) {
            int mm = MIRROR_OFF;
            if (parse_enum_table(v, k_mirror_tbl, &mm))
                next.mirror_mode = mm;
        } else if (!strcasecmp(k, "tail_length") || !strcasecmp(k, "tail_mode")) {
            int tm = TAIL_NORMAL;
            if (parse_enum_table(v, k_tail_tbl, &tm))
                next.tail_mode = tm;
        } else if (!strcasecmp(k, "col_density") || !strcasecmp(k, "dense")) {
            int dn = 0;
            if (parse_enum_table(v, k_bool_tbl, &dn))
                next.dense_cols = dn;
        } else if (!strcasecmp(k, "font_size")) {
            int fs = atoi(v);
            if (fs >= 5 && fs <= 32)
                next.font_size = fs;
        } else if (!strcasecmp(k, "bold")) {
            next.bold = atoi(v);
        } else if (!strcasecmp(k, "async")) {
            next.asynch = atoi(v);
        } else if (!strcasecmp(k, "fade")) {
            next.fade = atoi(v);
        } else if (!strcasecmp(k, "transition")) {
            if (!strcasecmp(v, "drain"))
                req_drain = true;
        }
    }
    fclose(f);

    if (req_drain) {
        app->pending_cfg = next;
        app->pending_cfg.paused = 0;
        app->has_pending_cfg = true;
        app->sim.draining = true;
        app->cfg.paused = 0;
        wp_reschedule_timer(app, 16);
    } else if (next.font_size != app->cfg.font_size || next.dense_cols != app->cfg.dense_cols) {
        app->cfg = next;
        app->cfg.paused = 0;
        app->has_pending_cfg = false;
        app->sim.draining = false;
        app->glow_dirty = true;
        wp_reinit_after_drain(app);
    } else {
        bool lum_changed = (fabsf(app->cfg.depth_parallax - next.depth_parallax) > 1e-4f) ||
                           (fabsf(app->cfg.speed_variance - next.speed_variance) > 1e-4f) ||
                           (app->cfg.bold != next.bold);
        bool vis_changed = lum_changed ||
                           (app->cfg.head_mode != next.head_mode) ||
                           (app->cfg.overlap_mode != next.overlap_mode) ||
                           (app->cfg.decay_mode != next.decay_mode) ||
                           (app->cfg.mirror_mode != next.mirror_mode) ||
                           (fabsf(app->cfg.glow - next.glow) > 1e-4f);
        app->sim.draining = false;
        app->has_pending_cfg = false;
        app->cfg = next;
        app->cfg.paused = 0;
        app->glow_dirty = vis_changed;
        if (lum_changed && app->sim.trails) {
            size_t n_slots = (size_t)app->sim.n_active * MAX_TRAILS_PER_COL;
            for (size_t i = 0; i < n_slots; i++) {
                if (app->sim.trails[i].flags & TRAIL_ACTIVE)
                    update_trail_luminescence(&app->sim.trails[i], &app->cfg);
            }
        }
        if (vis_changed && app->sim.col_dirty)
            memset(app->sim.col_dirty, 1, (size_t)app->sim.n_active * sizeof(uint8_t));
        if (next.spawn_rate > 0.001f) {
            int base_ms = sim_base_step_ms(&app->cfg);
            int max_cd = sim_max_space(app->sim.lines, next.spawn_rate) * base_ms;
            if (max_cd < base_ms) max_cd = base_ms;
            for (int k = 0; k < app->sim.n_active; k++) {
                if (app->sim.spawn_cd_ms[k] > max_cd)
                    app->sim.spawn_cd_ms[k] = (int32_t)(rng_u32(&app->sim.rngs[k], (uint32_t)max_cd) + 1u);
            }
        }
        wp_reschedule_timer(app, compute_optimal_timer_ms(&app->cfg, false));
    }
}

static gboolean on_unix_sig(gpointer user_data) {
    wallpaper_shutdown((WallpaperApp *)user_data);
    return G_SOURCE_REMOVE;
}

static gboolean on_sigusr1(gpointer user_data) {
    wp_reload_config((WallpaperApp *)user_data, false);
    return G_SOURCE_CONTINUE;
}

static gboolean on_sigusr2(gpointer user_data) {
    wp_reload_config((WallpaperApp *)user_data, true);
    return G_SOURCE_CONTINUE;
}

static gboolean on_ipc_accept(GIOChannel *src, GIOCondition cond, gpointer user_data) {
    (void)src;
    (void)cond;
    WallpaperApp *app = (WallpaperApp *)user_data;
    int cli = accept(app->sock_fd, NULL, NULL);
    if (cli < 0) return G_SOURCE_CONTINUE;

    char buf[512];
    ssize_t n = read(cli, buf, sizeof(buf) - 1);
    close(cli);
    if (n <= 0) return G_SOURCE_CONTINUE;
    buf[n] = '\0';

    if (strstr(buf, "\"quit\"") || strstr(buf, "quit"))
        wallpaper_shutdown(app);
    else if (strstr(buf, "\"pause\"") || strstr(buf, "pause"))
        app->cfg.paused = 1;
    else if (strstr(buf, "\"resume\"") || strstr(buf, "resume"))
        app->cfg.paused = 0;
    else if (strstr(buf, "\"drain\"") || strstr(buf, "drain"))
        wp_reload_config(app, true);
    else if (strstr(buf, "\"reload\"") || strstr(buf, "reload"))
        wp_reload_config(app, false);

    return G_SOURCE_CONTINUE;
}

static void start_ipc_socket(WallpaperApp *app) {
    app->sock_fd = -1;
    if (!app->cfg.ipc_socket[0]) return;
    unlink(app->cfg.ipc_socket);

    int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    if (fd < 0) return;

    struct sockaddr_un addr;
    memset(&addr, 0, sizeof(addr));
    addr.sun_family = AF_UNIX;
    if (strlen(app->cfg.ipc_socket) >= sizeof(addr.sun_path)) {
        close(fd);
        return;
    }
    snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", app->cfg.ipc_socket);

    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) != 0 || listen(fd, 8) != 0) {
        close(fd);
        return;
    }
    fcntl(fd, F_SETFL, O_NONBLOCK);
    struct stat st;
    if (stat(app->cfg.ipc_socket, &st) == 0)
        app->sock_ino = st.st_ino;

    app->sock_fd = fd;
    GIOChannel *ch = g_io_channel_unix_new(fd);
    app->sock_watch = g_io_add_watch(ch, G_IO_IN, on_ipc_accept, app);
    g_io_channel_unref(ch);
}

static int run_wallpaper_mode(WallpaperApp *app) {
    ensure_wayland_env();
    if (!gtk_init_check(NULL, NULL)) {
        fprintf(stderr, "nc-matrix: failed to initialize GTK3/Wayland display\n");
        return 1;
    }

    init_app_palette(app);
    app->running = true;

    GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    app->win = win;
    char title_buf[96];
    snprintf(title_buf, sizeof(title_buf), "nc-matrix-%s", app->cfg.output_name[0] ? app->cfg.output_name : "bg");
    gtk_window_set_title(GTK_WINDOW(win), title_buf);

    gtk_layer_init_for_window(GTK_WINDOW(win));
    gtk_layer_set_layer(GTK_WINDOW(win), GTK_LAYER_SHELL_LAYER_BACKGROUND);
    gtk_layer_set_exclusive_zone(GTK_WINDOW(win), -1);
    gtk_layer_set_keyboard_mode(GTK_WINDOW(win), GTK_LAYER_SHELL_KEYBOARD_MODE_NONE);
    gtk_layer_set_namespace(GTK_WINDOW(win), title_buf);
    gtk_layer_set_anchor(GTK_WINDOW(win), GTK_LAYER_SHELL_EDGE_TOP, TRUE);
    gtk_layer_set_anchor(GTK_WINDOW(win), GTK_LAYER_SHELL_EDGE_BOTTOM, TRUE);
    gtk_layer_set_anchor(GTK_WINDOW(win), GTK_LAYER_SHELL_EDGE_LEFT, TRUE);
    gtk_layer_set_anchor(GTK_WINDOW(win), GTK_LAYER_SHELL_EDGE_RIGHT, TRUE);

    GdkMonitor *mon = find_target_monitor(&app->cfg);
    if (mon) gtk_layer_set_monitor(GTK_WINDOW(win), mon);

    if (app->cfg.req_w > 0 && app->cfg.req_h > 0)
        gtk_window_set_default_size(GTK_WINDOW(win), app->cfg.req_w, app->cfg.req_h);

    gtk_widget_set_app_paintable(win, TRUE);
    gtk_widget_set_opacity(win, 1.0);
    app->sw_area = gtk_drawing_area_new();
    gtk_widget_set_app_paintable(app->sw_area, TRUE);
    g_signal_connect(app->sw_area, "draw", G_CALLBACK(on_sw_draw), app);
    gtk_container_add(GTK_CONTAINER(win), app->sw_area);

    write_pid_file(&app->cfg);
    start_ipc_socket(app);

    int tick_ms = compute_optimal_timer_ms(&app->cfg, false);
    app->timer_interval_ms = tick_ms;
    app->timer_id = g_timeout_add((guint)tick_ms, on_wallpaper_tick, app);

    g_unix_signal_add(SIGTERM, on_unix_sig, app);
    g_unix_signal_add(SIGINT,  on_unix_sig, app);
    g_unix_signal_add(SIGUSR1, on_sigusr1,  app);
    g_unix_signal_add(SIGUSR2, on_sigusr2,  app);
    g_signal_connect_swapped(win, "destroy", G_CALLBACK(wallpaper_shutdown), app);

    gtk_widget_show_all(win);
    gtk_main();

    wallpaper_shutdown(app);
    return 0;
}

/* ============================================================================
 * Headless Benchmark Mode (--benchmark FRAMES)
 * ============================================================================ */

static int run_benchmark_mode(WallpaperApp *app) {
    int w = app->cfg.req_w > 0 ? app->cfg.req_w : 5120;
    int h = app->cfg.req_h > 0 ? app->cfg.req_h : 1440;
    int frames = app->cfg.benchmark_frames > 0 ? app->cfg.benchmark_frames : 500;

    init_app_palette(app);
    atlas_build(&app->atlas, &app->cfg, 1);

    int cols = w / app->atlas.cell_w;
    int rows = h / app->atlas.cell_h;
    sim_init(&app->sim, rows, cols, &app->cfg);

    if (app->cfg.software)
        app->sw_surf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, w, h);

    int step_ms = sim_base_step_ms(&app->cfg);
    for (int i = 0; i < 300; i++)
        sim_step_dt(&app->sim, &app->cfg, step_ms);
    app->sim.total_spawns = 0;

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < frames; i++) {
        sim_step_dt(&app->sim, &app->cfg, step_ms);
        sync_grid_rgba(app);
        if (app->cfg.software)
            sync_sw_surface(app, false);
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);

    double elapsed_ms = ((double)(t1.tv_sec - t0.tv_sec) * 1000.0) +
                        ((double)(t1.tv_nsec - t0.tv_nsec) / 1e6);
    double ms_per_frame = elapsed_ms / (double)frames;
    int tick_ms = compute_optimal_timer_ms(&app->cfg, false);
    double target_fps = 1000.0 / (double)tick_ms;
    double est_cpu_pct = (ms_per_frame / (double)tick_ms) * 100.0;

    printf("nc-matrix benchmark (%s path):\n", app->cfg.software ? "software-dirty-tile" : "gpu-grid-pack");
    printf("  resolution : %dx%d @ %dpt (cell %dx%d px)\n", w, h, app->cfg.font_size, app->atlas.cell_w, app->atlas.cell_h);
    printf("  grid       : %dx%d (%d active streams, %d glyph slots)\n",
           cols, rows, app->sim.n_active, app->atlas.n_slots);
    printf("  frames     : %d in %.2f ms -> %.4f ms/frame (max %.0f FPS)\n",
           frames, elapsed_ms, ms_per_frame, 1000.0 / (ms_per_frame > 1e-6 ? ms_per_frame : 1e-6));
    printf("  target     : speed=%d (~%.1f FPS / %d ms) -> est. %.2f%% of 1 core\n",
           app->cfg.speed, target_fps, tick_ms, est_cpu_pct);
    const char *head_str = (app->cfg.head_mode == HEAD_WHITE) ? "white" : (app->cfg.head_mode == HEAD_BLEND) ? "blend" : "match";
    const char *ov_str = (app->cfg.overlap_mode == OVERLAP_PARALLAX) ? "parallax" : (app->cfg.overlap_mode == OVERLAP_ADDITIVE) ? "additive" : "hybrid";
    const char *dec_str = (app->cfg.decay_mode == DECAY_EXP) ? "exp" : "linear";
    printf("  spawns     : %llu (spawn_rate=%.2f, head=%s, var=%.1f, overlap=%s, glow=%.2f, decay=%s, dense=%d)\n",
           (unsigned long long)app->sim.total_spawns, (double)app->cfg.spawn_rate,
           head_str, (double)effective_speed_variance(&app->cfg), ov_str,
           (double)app->cfg.glow, dec_str, app->cfg.dense_cols);
    return 0;
}

/* ============================================================================
 * Terminal CLI Mode (Direct-ANSI Single-Write + Column-Major Diff Buffer)
 * ============================================================================ */

static void tty_sighandler(int sig) {
    g_tty_sig = sig;
}

static void write_all(int fd, const char *buf, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, buf + off, len - off);
        if (n > 0) {
            off += (size_t)n;
        } else if (n < 0 && errno == EINTR) {
            continue;
        } else {
            break;
        }
    }
}

static inline char *append_u32(char *dst, uint32_t v) {
    char tmp[12];
    int n = 0;
    do {
        tmp[n++] = (char)('0' + (v % 10u));
        v /= 10u;
    } while (v != 0u);
    while (n > 0)
        *dst++ = tmp[--n];
    return dst;
}

static inline char *emit_cup(char *dst, int r1, int c1) {
    *dst++ = '\x1b';
    *dst++ = '[';
    dst = append_u32(dst, (uint32_t)r1);
    *dst++ = ';';
    dst = append_u32(dst, (uint32_t)c1);
    *dst++ = 'H';
    return dst;
}

static bool tty_detect_truecolor(const Config *cfg) {
    if (cfg->force_term || cfg->console || getenv("NO_COLOR") != NULL)
        return false;
    const char *ct = getenv("COLORTERM");
    if (ct && (strstr(ct, "truecolor") || strstr(ct, "24bit")))
        return true;
    const char *term = getenv("TERM");
    if (term) {
        if (strstr(term, "ghostty") || strstr(term, "kitty") ||
            strstr(term, "wezterm") || strstr(term, "alacritty") ||
            strstr(term, "foot") || strstr(term, "-direct") ||
            strstr(term, "-24bit"))
            return true;
    }
    return false;
}

static inline char *emit_sgr(char *dst, int pair, int is_bold, int is_alt, int *cur_alt,
                             bool has_col, bool default_bg) {
    if (is_alt != *cur_alt) {
        *dst++ = '\x1b';
        *dst++ = '(';
        *dst++ = is_alt ? '0' : 'B';
        *cur_alt = is_alt;
    }
    *dst++ = '\x1b';
    *dst++ = '[';
    *dst++ = '0';
    if (is_bold) {
        *dst++ = ';';
        *dst++ = '1';
    }
    if (has_col) {
        int fg = 30 + (pair & 7);
        *dst++ = ';';
        *dst++ = (char)('0' + (fg / 10));
        *dst++ = (char)('0' + (fg % 10));
        if (!default_bg) {
            *dst++ = ';';
            *dst++ = '4';
            *dst++ = '0';
        }
    }
    *dst++ = 'm';
    return dst;
}

static inline char *emit_sgr_rgb(char *dst, uint32_t rgb24, int is_alt, int *cur_alt,
                                 bool default_bg) {
    if (is_alt != *cur_alt) {
        *dst++ = '\x1b';
        *dst++ = '(';
        *dst++ = is_alt ? '0' : 'B';
        *cur_alt = is_alt;
    }
    *dst++ = '\x1b';
    *dst++ = '[';
    *dst++ = '0';
    if (rgb24 != 0xFFFFFFFFu) {
        memcpy(dst, ";38;2;", 6);
        dst += 6;
        dst = append_u32(dst, (rgb24 >> 16) & 0xFFu);
        *dst++ = ';';
        dst = append_u32(dst, (rgb24 >> 8) & 0xFFu);
        *dst++ = ';';
        dst = append_u32(dst, rgb24 & 0xFFu);
    }
    if (!default_bg) {
        *dst++ = ';';
        *dst++ = '4';
        *dst++ = '0';
    }
    *dst++ = 'm';
    return dst;
}

static void tty_finish(const Config *cfg, int tty_fd) {
    static const char k_reset_seq[] = "\x1b(B\x1b[0m\x1b[?2026l\x1b[?25h";
    if (tty_fd >= 0)
        write_all(tty_fd, k_reset_seq, sizeof(k_reset_seq) - 1);
    curs_set(1);
    clear();
    refresh();
    resetty();
    endwin();
    if (cfg->console) {
        if (system("setfont >/dev/null 2>&1") != 0) {
            int rc = system("consolechars -d >/dev/null 2>&1");
            (void)rc;
        }
    }
}

static void tty_resize(MatrixSim *sim, Config *cfg, int tty_fd,
                       uint64_t **prev_buf, size_t *prev_cap,
                       char **tty_buf, size_t *tty_cap,
                       bool use_truecolor) {
#ifndef _WIN32
    const char *tty_dev = cfg->tty ? cfg->tty : ttyname(0);
    if (tty_dev) {
        int fd = open(tty_dev, O_RDWR);
        if (fd >= 0) {
            struct winsize win;
            if (ioctl(fd, TIOCGWINSZ, &win) == 0) {
                if (win.ws_col >= 10) COLS = win.ws_col;
                if (win.ws_row >= 10) LINES = win.ws_row;
            }
            close(fd);
        }
    }
#endif
    if (LINES < 10) LINES = 10;
    if (COLS < 10) COLS = 10;
#ifdef HAVE_RESIZETERM
    resizeterm(LINES, COLS);
#ifdef HAVE_WRESIZE
    wresize(stdscr, LINES, COLS);
#endif
#endif
    sim_init(sim, LINES, COLS, cfg);
    size_t total = (size_t)COLS * (size_t)LINES;
    if (total > *prev_cap) {
        uint64_t *np = realloc(*prev_buf, total * sizeof(uint64_t));
        if (!np) exit(1);
        *prev_buf = np;
        *prev_cap = total;
    }
    memset(*prev_buf, 0xFF, total * sizeof(uint64_t));

    size_t per_cell = use_truecolor ? 44u : 18u;
    size_t need_cap = total * per_cell + 8192u;
    if (need_cap > *tty_cap) {
        char *nb = realloc(*tty_buf, need_cap);
        if (!nb) exit(1);
        *tty_buf = nb;
        *tty_cap = need_cap;
    }

    clear();
    refresh();
    static const char k_clr[] = "\x1b[0m\x1b[2J\x1b[H\x1b[?25l";
    write_all(tty_fd, k_clr, sizeof(k_clr) - 1);
}

static void tty_render_dirty(WallpaperApp *app, int tty_fd,
                             uint64_t *prev_buf, char *tty_buf,
                             bool has_col, bool default_bg, bool use_truecolor) {
    MatrixSim *s = &app->sim;
    const Config *cfg = &app->cfg;
    int max_r = cfg->oldstyle ? (LINES - 1) : LINES;
    if (max_r > s->lines) max_r = s->lines;

    uint32_t cfg_tag = ((uint32_t)(cfg->mcolor & 63))
                     | ((uint32_t)((cfg->bold + 1) & 3) << 6)
                     | ((uint32_t)(cfg->rainbow & 1) << 8)
                     | ((uint32_t)(cfg->charset & 7) << 9)
                     | ((uint32_t)(cfg->console & 1) << 12)
                     | ((uint32_t)(cfg->head_mode & 3) << 13)
                     | ((uint32_t)(cfg->overlap_mode & 3) << 15)
                     | ((uint32_t)(cfg->decay_mode & 1) << 17)
                     | ((uint32_t)(use_truecolor ? 1 : 0) << 18);
    if (cfg->xwindow) cfg_tag ^= (1u << 12);
    if (cfg->oldstyle) cfg_tag ^= (1u << 19);
    uint32_t glow_u8 = (uint32_t)lroundf(fmaxf(0.0f, fminf(1.0f, cfg->glow)) * 255.0f);
    uint32_t par_q   = (uint32_t)lroundf(cfg->depth_parallax * 100.0f) & 0xFFu;
    cfg_tag ^= (glow_u8 << 20) ^ (par_q << 24);
    bool cfg_changed = (cfg_tag != s->last_tty_cfg_tag);
    s->last_tty_cfg_tag = cfg_tag;

    char *p = tty_buf;
    memcpy(p, "\x1b[?2026h", 8);
    p += 8;

    int cur_r = -1;
    int cur_c = -1;
    int cur_pair = -1;
    int cur_bold = -1;
    uint32_t cur_rgb24 = 0xFFFFFFFEu;
    int cur_alt = 0;
    bool emitted = false;
    const size_t stride_lines = (size_t)LINES;
    const uint8_t *wht = app->palette_u8[1];

    for (int k = 0; k < s->n_active; k++) {
        if (!s->col_dirty[k] && !cfg_changed) continue;
        s->col_dirty[k] = 0;

        int j = k * s->col_step;
        if (j >= COLS || j >= s->cols) continue;
        size_t col_base = (size_t)j * stride_lines;

        uint16_t row_cp[MAX_SIM_ROWS];
        uint8_t  row_cid[MAX_SIM_ROWS];
        uint8_t  row_fade[MAX_SIM_ROWS];
        uint8_t  row_wh[MAX_SIM_ROWS];
        uint8_t  row_mir[MAX_SIM_ROWS];
        uint8_t  row_flags[MAX_SIM_ROWS];
        composite_column(s, cfg, k, max_r, !use_truecolor,
                         row_cp, row_cid, row_fade, row_wh, row_mir, row_flags);

        for (int r = 0; r < max_r; r++) {
            bool active = (row_flags[r] & 1u) != 0u;
            bool is_bold = (row_flags[r] & 2u) != 0u;
            uint64_t raw_sig;
            uint32_t rgb24 = 0xFFFFFFFFu;
            int is_alt = 0;
            int pair = cfg->mcolor;

            if (use_truecolor) {
                if (!active) {
                    raw_sig = 0x0020ULL | (1ULL << 48);
                } else {
                    is_alt = (cfg->console || cfg->xwindow) ? 1 : 0;
                    int st = (row_cid[r] == 255u) ? 1 : ((is_bold ? 65 : 2) + row_cid[r]);
                    const uint8_t *base = app->palette_u8[st];
                    uint32_t fade = row_fade[r] ? row_fade[r] : 1u;
                    uint32_t bloom = (uint32_t)row_wh[r] + ((((fade * fade) >> 8) * glow_u8 * 56u) >> 16);
                    if (bloom > 255u) bloom = 255u;
                    uint32_t ib = 255u - bloom;
                    uint32_t R = (((base[0] * ib + wht[0] * bloom) / 255u) * fade + 127u) / 255u;
                    uint32_t G = (((base[1] * ib + wht[1] * bloom) / 255u) * fade + 127u) / 255u;
                    uint32_t B = (((base[2] * ib + wht[2] * bloom) / 255u) * fade + 127u) / 255u;
                    rgb24 = (R << 16) | (G << 8) | B;
                    raw_sig = (uint64_t)row_cp[r]
                            | ((uint64_t)(is_alt & 1) << 16)
                            | ((uint64_t)rgb24 << 24);
                }
            } else {
                if (!active) {
                    raw_sig = 0x0020ULL;
                } else {
                    bool wh_8 = (row_flags[r] & 4u) && cfg->head_mode == HEAD_BLEND && row_wh[r] >= 128u;
                    pair = wh_8 ? COLOR_WHITE : row_cid[r];
                    raw_sig = (uint64_t)row_cp[r] | ((uint64_t)(is_bold ? 1 : 0) << 16) |
                              (((uint64_t)pair & 7ULL) << 17) | ((uint64_t)cfg_tag << 20);
                }
            }

            size_t idx = col_base + (size_t)r;
            if (prev_buf[idx] == raw_sig) continue;
            prev_buf[idx] = raw_sig;

            wchar_t wc = L' ';
            if (active) {
                is_alt = (cfg->console || cfg->xwindow) ? 1 : 0;
                wc = (cfg->oldstyle && row_cp[r] == '&' && is_alt) ? 183 : (wchar_t)row_cp[r];
            }

            if (r != cur_r || j != cur_c)
                p = emit_cup(p, r + 1, j + 1);
            if (use_truecolor) {
                if (rgb24 != cur_rgb24 || is_alt != cur_alt) {
                    p = emit_sgr_rgb(p, rgb24, is_alt, &cur_alt, default_bg);
                    cur_rgb24 = rgb24;
                }
            } else {
                int bld_i = (active && is_bold) ? 1 : 0;
                if (pair != cur_pair || bld_i != cur_bold || is_alt != cur_alt) {
                    p = emit_sgr(p, pair, bld_i, is_alt, &cur_alt, has_col, default_bg);
                    cur_pair = pair;
                    cur_bold = bld_i;
                }
            }

            if (is_alt && wc < 256)
                *p++ = (char)(unsigned char)wc;
            else
                p += utf8_encode((uint16_t)wc, p);

            cur_r = r;
            cur_c = j + 1;
            emitted = true;
        }
    }

    if (cfg->msg[0]) {
        int mlen = (int)strlen(cfg->msg);
        int mx = LINES / 2;
        int my = COLS / 2 - mlen / 2;
        bool msg_dirty = false;
        for (int dr = -1; dr <= 1 && !msg_dirty; dr++) {
            int rr = mx + dr;
            if (rr < 0 || rr >= LINES) continue;
            for (int dc = -2; dc < mlen + 2; dc++) {
                int cc = my + dc;
                if (cc >= 0 && cc < COLS &&
                    prev_buf[(size_t)cc * stride_lines + (size_t)rr] != 0xFFFFFFFFFFFFFFFEULL) {
                    msg_dirty = true;
                    break;
                }
            }
        }
        if (msg_dirty || emitted) {
            int box_c0 = my - 2;
            int box_c1 = my + mlen + 2;
            if (box_c0 < 0) box_c0 = 0;
            if (box_c1 > COLS) box_c1 = COLS;
            if (use_truecolor) {
                const uint8_t *mc = app->palette_u8[65 + (cfg->mcolor % PALETTE_COLORS)];
                uint32_t msg_rgb = ((uint32_t)mc[0] << 16) | ((uint32_t)mc[1] << 8) | (uint32_t)mc[2];
                p = emit_sgr_rgb(p, msg_rgb, 0, &cur_alt, default_bg);
                cur_rgb24 = msg_rgb;
            } else {
                p = emit_sgr(p, cfg->mcolor, 1, 0, &cur_alt, has_col, default_bg);
                cur_pair = cfg->mcolor;
                cur_bold = 1;
            }
            for (int dr = -1; dr <= 1; dr++) {
                int rr = mx + dr;
                if (rr < 0 || rr >= LINES) continue;
                int span = box_c1 - box_c0;
                if (span <= 0) continue;
                p = emit_cup(p, rr + 1, box_c0 + 1);
                if (dr == 0) {
                    for (int cc = box_c0; cc < box_c1; cc++) {
                        int mpos = cc - my;
                        *p++ = (mpos >= 0 && mpos < mlen) ? cfg->msg[mpos] : ' ';
                    }
                } else {
                    memset(p, ' ', (size_t)span);
                    p += span;
                }
                for (int cc = box_c0; cc < box_c1; cc++)
                    prev_buf[(size_t)cc * stride_lines + (size_t)rr] = 0xFFFFFFFFFFFFFFFEULL;
            }
            emitted = true;
        }
    }

    if (emitted) {
        if (cur_alt) {
            *p++ = '\x1b';
            *p++ = '(';
            *p++ = 'B';
        }
        memcpy(p, "\x1b[?2026l", 8);
        p += 8;
        write_all(tty_fd, tty_buf, (size_t)(p - tty_buf));
    }
}

static int run_tty_mode(WallpaperApp *app) {
    Config *cfg = &app->cfg;
    if (cfg->force_term && strcmp("linux", getenv("TERM") ? getenv("TERM") : "") != 0)
        setenv("TERM", "linux", 1);

    int tty_fd = STDOUT_FILENO;
    FILE *ftty = NULL;
    SCREEN *scr = NULL;
    if (cfg->tty) {
        ftty = fopen(cfg->tty, "r+");
        if (!ftty) {
            fprintf(stderr, "nc-matrix: error: '%s' couldn't be opened: %s.\n", cfg->tty, strerror(errno));
            return EXIT_FAILURE;
        }
        tty_fd = fileno(ftty);
        scr = newterm(NULL, ftty, ftty);
        if (!scr) {
            fclose(ftty);
            return EXIT_FAILURE;
        }
        set_term(scr);
    } else {
        initscr();
    }

    savetty();
    nonl();
    cbreak();
    noecho();
    timeout(0);
    leaveok(stdscr, TRUE);
    curs_set(0);

    signal(SIGINT,  tty_sighandler);
    signal(SIGQUIT, tty_sighandler);
    signal(SIGWINCH, tty_sighandler);
    signal(SIGTSTP, tty_sighandler);

    if (cfg->console) {
        if (system("setfont matrix >/dev/null 2>&1") != 0 &&
            system("consolechars -f matrix >/dev/null 2>&1") != 0) {
            tty_finish(cfg, tty_fd);
            if (scr) delscreen(scr);
            if (ftty) fclose(ftty);
            fprintf(stderr, "nc-matrix: failed to load matrix console font.\n");
            return 1;
        }
    }

    bool use_truecolor = tty_detect_truecolor(cfg);
    init_app_palette(app);
    if (use_truecolor && cfg->has_custom_rgb)
        register_custom_hex_color(app, cfg, cfg->custom_rgb);

    bool has_col = false;
    bool default_bg = true;
    if (has_colors()) {
        has_col = true;
        start_color();
        if (use_default_colors() != ERR) {
            default_bg = true;
            for (int c = 0; c <= 7; c++) init_pair(c, c, -1);
        } else {
            default_bg = false;
            for (int c = 0; c <= 7; c++) init_pair(c, c, COLOR_BLACK);
        }
    }

    /* Single initial refresh so stdscr stays clean and wgetch() skips doupdate() */
    clear();
    refresh();
    static const char k_init_seq[] = "\x1b[0m\x1b[?25l";
    write_all(tty_fd, k_init_seq, sizeof(k_init_seq) - 1);

    size_t prev_cap = (size_t)COLS * (size_t)LINES;
    uint64_t *prev_buf = malloc(prev_cap * sizeof(uint64_t));
    size_t per_cell = use_truecolor ? 44u : 18u;
    size_t tty_cap = prev_cap * per_cell + 8192u;
    char *tty_buf = malloc(tty_cap);
    if (!prev_buf || !tty_buf) {
        fprintf(stderr, "nc-matrix: out of memory allocating TTY buffers\n");
        free(prev_buf);
        free(tty_buf);
        tty_finish(cfg, tty_fd);
        if (scr) delscreen(scr);
        if (ftty) fclose(ftty);
        return EXIT_FAILURE;
    }
    memset(prev_buf, 0xFF, prev_cap * sizeof(uint64_t));
    sim_init(&app->sim, LINES, COLS, cfg);

    struct timespec prev_ts;
    clock_gettime(CLOCK_MONOTONIC, &prev_ts);
    bool force_redraw = true;

    for (;;) {
        struct timespec t_start, t_end;
        clock_gettime(CLOCK_MONOTONIC, &t_start);
        long long dt_ns = ((long long)(t_start.tv_sec - prev_ts.tv_sec) * 1000000000LL) +
                          (long long)(t_start.tv_nsec - prev_ts.tv_nsec);
        int dt_ms = (int)(dt_ns / 1000000LL);
        if (dt_ms < 0) dt_ms = 0;
        if (dt_ms > 250) dt_ms = 250;
        if (dt_ms > 0) {
            long long add_ns = (long long)dt_ms * 1000000LL;
            prev_ts.tv_sec  += (time_t)(add_ns / 1000000000LL);
            prev_ts.tv_nsec += (long)(add_ns % 1000000000LL);
            if (prev_ts.tv_nsec >= 1000000000L) {
                prev_ts.tv_sec++;
                prev_ts.tv_nsec -= 1000000000L;
            }
        }

        if ((g_tty_sig == SIGINT || g_tty_sig == SIGQUIT || g_tty_sig == SIGTSTP) && cfg->lock != 1)
            break;
        if (g_tty_sig == SIGWINCH) {
            g_tty_sig = 0;
            tty_resize(&app->sim, cfg, tty_fd, &prev_buf, &prev_cap, &tty_buf, &tty_cap, use_truecolor);
            force_redraw = true;
        }

        int kp = wgetch(stdscr);
        if (kp != ERR) {
            if (cfg->screensaver) break;
            bool refresh_lum = false;
            force_redraw = true;
            switch (kp) {
            case 'q': if (cfg->lock != 1) goto done; break;
            case 'a': cfg->asynch = !cfg->asynch; cfg->has_speed_var = 0; refresh_lum = true; break;
            case 'b': cfg->bold = 1; refresh_lum = true; break;
            case 'B': cfg->bold = 2; refresh_lum = true; break;
            case 'L': cfg->lock = 1; break;
            case 'n': cfg->bold = 0; refresh_lum = true; break;
            case 'k': cfg->shimmer_mode = (cfg->shimmer_mode + 1) % 5; cfg->has_shimmer = 1; break;
            case '+':
            case '=':
                cfg->has_u = 0;
                if (cfg->speed < 10) cfg->speed++;
                break;
            case '-':
            case '_':
                cfg->has_u = 0;
                if (cfg->speed > 1) cfg->speed--;
                break;
            case 'v':
                cfg->has_speed_var = 1;
                cfg->speed_variance = fmaxf(0.0f, cfg->speed_variance - 1.0f);
                refresh_lum = true;
                break;
            case 'V':
                cfg->has_speed_var = 1;
                cfg->speed_variance = fminf(10.0f, cfg->speed_variance + 1.0f);
                refresh_lum = true;
                break;
            case 'c':
                cfg->charset = (cfg->charset + 1) % 5;
                break;
            case 'd':
                cfg->dense_cols = !cfg->dense_cols;
                sim_init(&app->sim, LINES, COLS, cfg);
                memset(prev_buf, 0xFF, prev_cap * sizeof(uint64_t));
                break;
            case 'h':
                cfg->head_mode = (cfg->head_mode + 1) % 3;
                break;
            case '0': cfg->update = 0; cfg->has_u = 1; break;
            case '1' ... '9': cfg->update = kp - '0'; cfg->has_u = 1; break;
            case '!': cfg->mcolor = COLOR_RED;     cfg->rainbow = 0; break;
            case '@': cfg->mcolor = COLOR_GREEN;   cfg->rainbow = 0; break;
            case '#': cfg->mcolor = COLOR_YELLOW;  cfg->rainbow = 0; break;
            case '$': cfg->mcolor = COLOR_BLUE;    cfg->rainbow = 0; break;
            case '%': cfg->mcolor = COLOR_MAGENTA; cfg->rainbow = 0; break;
            case '^': cfg->mcolor = COLOR_CYAN;    cfg->rainbow = 0; break;
            case '&': cfg->mcolor = COLOR_WHITE;   cfg->rainbow = 0; break;
            case 'r':
                cfg->rainbow = !cfg->rainbow;
                for (int k = 0; k < app->sim.n_active; k++) {
                    MatrixTrail *col_tr = &app->sim.trails[(size_t)k * MAX_TRAILS_PER_COL];
                    for (int ti = 0; ti < MAX_TRAILS_PER_COL; ti++) {
                        if (col_tr[ti].flags & TRAIL_ACTIVE) {
                            if (cfg->rainbow) col_tr[ti].flags |= TRAIL_RAINBOW;
                            else col_tr[ti].flags &= (uint8_t)~TRAIL_RAINBOW;
                        }
                    }
                }
                break;
            case 'm': cfg->charset = (cfg->charset == CS_LAMBDA) ? CS_ASCII : CS_LAMBDA; break;
            case 'p':
            case 'P': cfg->paused = !cfg->paused; break;
            default: break;
            }
            if (refresh_lum) {
                for (int k = 0; k < app->sim.n_active; k++) {
                    MatrixTrail *col_tr = &app->sim.trails[(size_t)k * MAX_TRAILS_PER_COL];
                    for (int ti = 0; ti < MAX_TRAILS_PER_COL; ti++) {
                        if (col_tr[ti].flags & TRAIL_ACTIVE)
                            update_trail_luminescence(&col_tr[ti], cfg);
                    }
                }
            }
        }

        bool stepped = sim_step_dt(&app->sim, cfg, dt_ms);
        if (stepped || force_redraw) {
            tty_render_dirty(app, tty_fd, prev_buf, tty_buf, has_col, default_bg, use_truecolor);
            force_redraw = false;
        }

        int target_ms = compute_optimal_timer_ms(cfg, false);
        clock_gettime(CLOCK_MONOTONIC, &t_end);
        long long elapsed_ns = ((long long)(t_end.tv_sec - t_start.tv_sec) * 1000000000LL) +
                               (long long)(t_end.tv_nsec - t_start.tv_nsec);
        long long rem_ns = (long long)target_ms * 1000000LL - elapsed_ns;
        if (rem_ns > 0) {
            struct timespec req;
            req.tv_sec = (time_t)(rem_ns / 1000000000LL);
            req.tv_nsec = (long)(rem_ns % 1000000000LL);
            while (nanosleep(&req, &req) == -1 && errno == EINTR) {
                if (g_tty_sig != 0) break;
            }
        }
    }

done:
    free(tty_buf);
    free(prev_buf);
    tty_finish(cfg, tty_fd);
    if (scr) delscreen(scr);
    if (ftty) fclose(ftty);
    return 0;
}

/* ============================================================================
 * CLI Argument Parsing & Main
 * ============================================================================ */

static void print_usage(void) {
    printf("%s\n\n", NCMATRIX_VERSION);
    printf("Usage: nc-matrix [OPTIONS]\n\n");
    printf("Classic cmatrix-compatible flags:\n");
    printf("  -a                  Asynchronous trail speeds (default: on)\n");
    printf("  -b / -B / -n        Partial bold / all bold (default) / no bold characters\n");
    printf("  -c                  Japanese half-width Katakana + digits (default)\n");
    printf("  -C <color|hex|rb>   Color: green, cyan, yellow, white, red, blue, magenta,\n");
    printf("                      #RRGGBB, or rainbow (default: green)\n");
    printf("  -f                  Force linux TERM type\n");
    printf("  -l                  Linux console matrix font\n");
    printf("  -L                  Lock mode (disable 'q' quit)\n");
    printf("  -M <message>        Display centered locked message\n");
    printf("  -m                  Lambda (λ) glyph mode\n");
    printf("  -o                  Use old-style scrolling\n");
    printf("  -r                  Rainbow color mode\n");
    printf("  -s                  Screensaver mode (exit on any key)\n");
    printf("  -t <tty>            Render onto a specific TTY device\n");
    printf("  -u <0..20>          Override cadence via cmatrix delay (delay * 10 ms)\n");
    printf("  -x                  X window matrix font mode\n");
    printf("  -k                  Toggle / cycle glyph shimmer\n");
    printf("  -h, --help          Show this help message and exit\n");
    printf("  -V, --version       Print version and exit\n\n");
    printf("Preset & Rain Physics Options (default tuned to optimal Matrix look):\n");
    printf("  --classic                 Restore vintage sparse cmatrix defaults\n");
    printf("  --charset <MODE>          katakana (default), ascii, binary, hex, lambda\n");
    printf("  --color <COL>             green (default), cyan, yellow, white, red, blue,\n");
    printf("                            magenta, rainbow, or #RRGGBB\n");
    printf("  --head-color <MODE>       white, match (shader), or blend (default)\n");
    printf("  --speed <1..10>           Base speed: 1=520ms .. 5=200ms (default) .. 10=20ms\n");
    printf("  --spawn-rate <0.0..3.0>   Trail spawn multiplier (default: 1.5)\n");
    printf("  --speed-variance <0..10>  Per-trail geometric spread (default: 5.0)\n");
    printf("  --overlap <MODE>          hybrid (default), parallax, additive\n");
    printf("  --parallax <0.0..1.0>     Speed luminescence & blend attenuation (default: 0.50)\n");
    printf("  --glow <0.0..1.0>         CRT phosphor halo intensity (default: 0.60)\n");
    printf("  --decay <MODE>            exponential (default) or linear\n");
    printf("  --shimmer <MODE>          off, low (0.5/s), med (1/s), high (2/s), insane (default)\n");
    printf("  --mirror <MODE>           off, on, random (default)\n");
    printf("  --tail-length <MODE>      short, normal, long (default)\n");
    printf("  --dense <0|1|on|off>      0=classic gutter, 1=every column (default: 1)\n\n");
    printf("Wayland Layer-Shell Wallpaper & Hot-Reload Options:\n");
    printf("  --wallpaper               Run fullscreen on wlr-layer-shell background\n");
    printf("  --output <NAME>           Target monitor output (e.g. DP-1)\n");
    printf("  --font-size <5..32>       Glyph size in pt (default: 9)\n");
    printf("  --x <X> --y <Y>           Monitor logical origin\n");
    printf("  --width <W> --height <H>  Monitor pixel dimensions\n");
    printf("  --pid-file <PATH>         Write daemon PID file\n");
    printf("  --ipc-socket <PATH>       UNIX control socket (quit, pause, resume)\n");
    printf("  --software                Force CPU dirty-tile Cairo fallback renderer\n");
    printf("  --benchmark <FRAMES>      Headless FPS/CPU throughput benchmark\n\n");
    printf("Hot-Reload Signals (Wallpaper mode):\n");
    printf("  Reads /tmp/nc-matrix_<output>.conf on SIGUSR1 (flow) or SIGUSR2 (drain).\n\n");
    printf("Interactive TTY Keys:\n");
    printf("  q         Quit (unless -L)      p/P       Pause / resume\n");
    printf("  +/-       Faster / slower       v/V       Decrease / increase speed variance\n");
    printf("  0-9       cmatrix delay * 10ms  c         Cycle charset\n");
    printf("  k         Cycle shimmer mode    h         Cycle trail head mode\n");
    printf("  d         Toggle dense grid     r         Toggle rainbow\n");
    printf("  ! @ # $ %% ^ &   Set color (Red Green Yellow Blue Magenta Cyan White)\n\n");
    printf("Examples:\n");
    printf("  nc-matrix                                  # Full Matrix rain in terminal\n");
    printf("  nc-matrix --classic -C cyan                # Classic sparse cyan terminal\n");
    printf("  nc-matrix --wallpaper --output DP-1        # Live EGL desktop wallpaper\n");
    printf("  nc-matrix --benchmark 120 --width 2560 --height 1440\n");
}

enum {
    OPT_WALLPAPER = 1000,
    OPT_CLASSIC,
    OPT_OUTPUT,
    OPT_CHARSET,
    OPT_COLOR_LONG,
    OPT_SPEED,
    OPT_SPAWN_RATE,
    OPT_HEAD_COLOR,
    OPT_SPEED_VAR,
    OPT_OVERLAP,
    OPT_PARALLAX,
    OPT_GLOW,
    OPT_DECAY,
    OPT_SHIMMER,
    OPT_MIRROR,
    OPT_TAIL_LENGTH,
    OPT_DENSE,
    OPT_FONT_SIZE,
    OPT_WIDTH,
    OPT_HEIGHT,
    OPT_POS_X,
    OPT_POS_Y,
    OPT_PID_FILE,
    OPT_IPC_SOCKET,
    OPT_SOFTWARE,
    OPT_BENCHMARK,
};

int main(int argc, char *argv[]) {
    setlocale(LC_ALL, "");

    WallpaperApp *app = calloc(1, sizeof(WallpaperApp));
    if (!app) return 1;
    app->sock_fd = -1;
    app->egl_dpy = EGL_NO_DISPLAY;
    app->egl_ctx = EGL_NO_CONTEXT;
    app->egl_surf = EGL_NO_SURFACE;
    app->cfg.update = 4;
    app->cfg.speed = 5;
    app->cfg.fade = 1;
    app->cfg.bold = 2;
    app->cfg.asynch = 1;
    app->cfg.mcolor = COLOR_GREEN;
    app->cfg.font_size = 9;
    app->cfg.charset = CS_KATAKANA;
    app->cfg.spawn_rate = 1.5f;
    app->cfg.head_mode = HEAD_BLEND;
    app->cfg.speed_variance = 5.0f;
    app->cfg.has_speed_var = 1;
    app->cfg.overlap_mode = OVERLAP_HYBRID;
    app->cfg.depth_parallax = 0.50f;
    app->cfg.glow = 0.60f;
    app->cfg.decay_mode = DECAY_EXP;
    app->cfg.shimmer_mode = SHIMMER_INSANE;
    app->cfg.has_shimmer = 1;
    app->cfg.mirror_mode = MIRROR_RANDOM;
    app->cfg.tail_mode = TAIL_LONG;
    app->cfg.dense_cols = 1;

    static const struct option long_opts[] = {
        {"wallpaper",      no_argument,       NULL, OPT_WALLPAPER},
        {"classic",        no_argument,       NULL, OPT_CLASSIC},
        {"output",         required_argument, NULL, OPT_OUTPUT},
        {"charset",        required_argument, NULL, OPT_CHARSET},
        {"color",          required_argument, NULL, OPT_COLOR_LONG},
        {"speed",          required_argument, NULL, OPT_SPEED},
        {"spawn-rate",     required_argument, NULL, OPT_SPAWN_RATE},
        {"head-color",     required_argument, NULL, OPT_HEAD_COLOR},
        {"speed-variance", required_argument, NULL, OPT_SPEED_VAR},
        {"overlap",        required_argument, NULL, OPT_OVERLAP},
        {"parallax",       required_argument, NULL, OPT_PARALLAX},
        {"glow",           required_argument, NULL, OPT_GLOW},
        {"decay",          required_argument, NULL, OPT_DECAY},
        {"shimmer",        required_argument, NULL, OPT_SHIMMER},
        {"mirror",         required_argument, NULL, OPT_MIRROR},
        {"tail-length",    required_argument, NULL, OPT_TAIL_LENGTH},
        {"dense",          required_argument, NULL, OPT_DENSE},
        {"font-size",      required_argument, NULL, OPT_FONT_SIZE},
        {"width",          required_argument, NULL, OPT_WIDTH},
        {"height",         required_argument, NULL, OPT_HEIGHT},
        {"x",              required_argument, NULL, OPT_POS_X},
        {"y",              required_argument, NULL, OPT_POS_Y},
        {"pid-file",       required_argument, NULL, OPT_PID_FILE},
        {"ipc-socket",     required_argument, NULL, OPT_IPC_SOCKET},
        {"software",       no_argument,       NULL, OPT_SOFTWARE},
        {"benchmark",      required_argument, NULL, OPT_BENCHMARK},
        {"version",        no_argument,       NULL, 'V'},
        {"help",           no_argument,       NULL, 'h'},
        {NULL, 0, NULL, 0}
    };

    opterr = 0;
    int opt;
    while ((opt = getopt_long(argc, argv, "abBcfhlLnrosmxkVM:u:C:t:", long_opts, NULL)) != -1) {
        switch (opt) {
        case 'a': app->cfg.asynch = 1; break;
        case 'b': if (app->cfg.bold != 2) app->cfg.bold = 1; break;
        case 'B': app->cfg.bold = 2; break;
        case 'n': app->cfg.bold = -1; break;
        case 'c': app->cfg.charset = CS_KATAKANA; break;
        case 'f': app->cfg.force_term = 1; break;
        case 'l': app->cfg.console = 1; break;
        case 'L':
            app->cfg.lock = 1;
            if (!app->cfg.msg[0]) snprintf(app->cfg.msg, sizeof(app->cfg.msg), "Computer locked.");
            break;
        case 'M': snprintf(app->cfg.msg, sizeof(app->cfg.msg), "%s", optarg); break;
        case 'o': app->cfg.oldstyle = 1; break;
        case 's': app->cfg.screensaver = 1; break;
        case 'x': app->cfg.xwindow = 1; break;
        case 'r': app->cfg.rainbow = 1; break;
        case 'm': app->cfg.charset = CS_LAMBDA; break;
        case 'k': app->cfg.changes = 1; break;
        case 't': app->cfg.tty = optarg; break;
        case 'u':
            app->cfg.update = atoi(optarg);
            if (app->cfg.update < 0) app->cfg.update = 0;
            if (app->cfg.update > 20) app->cfg.update = 20;
            app->cfg.has_u = 1;
            break;
        case 'C':
        case OPT_COLOR_LONG:
            if (!strcasecmp(optarg, "rainbow")) {
                app->cfg.rainbow = 1;
                app->cfg.has_custom_rgb = false;
            } else {
                uint8_t rgb[3];
                if (parse_hex_rgb(optarg, rgb)) {
                    memcpy(app->cfg.custom_rgb, rgb, 3);
                    app->cfg.has_custom_rgb = true;
                    app->cfg.rainbow = 0;
                    app->cfg.mcolor = nearest_tty_color(rgb);
                } else {
                    int c = color_from_name(optarg);
                    if (c < 0) {
                        fprintf(stderr, "nc-matrix: invalid color '%s'\n", optarg);
                        free(app);
                        return 1;
                    }
                    app->cfg.mcolor = c;
                    app->cfg.rainbow = 0;
                    app->cfg.has_custom_rgb = false;
                }
            }
            break;
        case OPT_WALLPAPER:  app->cfg.wallpaper = 1; break;
        case OPT_CLASSIC:
            app->cfg.charset = CS_ASCII;
            app->cfg.dense_cols = 0;
            app->cfg.speed = 7;
            app->cfg.spawn_rate = 1.0f;
            app->cfg.head_mode = HEAD_WHITE;
            app->cfg.speed_variance = 0.0f;
            app->cfg.has_speed_var = 1;
            app->cfg.depth_parallax = 0.0f;
            app->cfg.glow = 0.0f;
            app->cfg.decay_mode = DECAY_LINEAR;
            app->cfg.shimmer_mode = SHIMMER_OFF;
            app->cfg.mirror_mode = MIRROR_OFF;
            app->cfg.tail_mode = TAIL_NORMAL;
            app->cfg.bold = 1;
            app->cfg.asynch = 0;
            break;
        case OPT_OUTPUT:     snprintf(app->cfg.output_name, sizeof(app->cfg.output_name), "%s", optarg); break;
        case OPT_CHARSET:    app->cfg.charset = charset_from_name(optarg); break;
        case OPT_SPEED:
            app->cfg.speed = atoi(optarg);
            break;
        case OPT_SPAWN_RATE:
            parse_float_clamped(optarg, 0.0f, 3.0f, &app->cfg.spawn_rate);
            break;
        case OPT_HEAD_COLOR:
            parse_enum_table(optarg, k_head_tbl, &app->cfg.head_mode);
            break;
        case OPT_SPEED_VAR:
            parse_float_clamped(optarg, 0.0f, 10.0f, &app->cfg.speed_variance);
            app->cfg.has_speed_var = 1;
            break;
        case OPT_OVERLAP:
            parse_enum_table(optarg, k_overlap_tbl, &app->cfg.overlap_mode);
            break;
        case OPT_PARALLAX:
            parse_parallax_strength(optarg, &app->cfg.depth_parallax);
            break;
        case OPT_GLOW:
            parse_float_clamped(optarg, 0.0f, 1.0f, &app->cfg.glow);
            break;
        case OPT_DECAY:
            parse_enum_table(optarg, k_decay_tbl, &app->cfg.decay_mode);
            break;
        case OPT_SHIMMER:
            parse_enum_table(optarg, k_shimmer_tbl, &app->cfg.shimmer_mode);
            app->cfg.has_shimmer = 1;
            break;
        case OPT_MIRROR:
            parse_enum_table(optarg, k_mirror_tbl, &app->cfg.mirror_mode);
            break;
        case OPT_TAIL_LENGTH:
            parse_enum_table(optarg, k_tail_tbl, &app->cfg.tail_mode);
            break;
        case OPT_DENSE:
            parse_enum_table(optarg, k_bool_tbl, &app->cfg.dense_cols);
            break;
        case OPT_FONT_SIZE:  app->cfg.font_size = atoi(optarg); break;
        case OPT_WIDTH:      app->cfg.req_w = atoi(optarg); break;
        case OPT_HEIGHT:     app->cfg.req_h = atoi(optarg); break;
        case OPT_POS_X:      app->cfg.req_x = atoi(optarg); app->cfg.has_pos = 1; break;
        case OPT_POS_Y:      app->cfg.req_y = atoi(optarg); app->cfg.has_pos = 1; break;
        case OPT_PID_FILE:   snprintf(app->cfg.pid_file, sizeof(app->cfg.pid_file), "%s", optarg); break;
        case OPT_IPC_SOCKET: snprintf(app->cfg.ipc_socket, sizeof(app->cfg.ipc_socket), "%s", optarg); break;
        case OPT_SOFTWARE:   app->cfg.software = 1; break;
        case OPT_BENCHMARK:  app->cfg.benchmark_frames = atoi(optarg); break;
        case 'V':
            printf("%s\n", NCMATRIX_VERSION);
            free(app);
            return 0;
        case 'h':
        case '?':
            print_usage();
            free(app);
            return 0;
        }
    }

    int rc;
    if (app->cfg.benchmark_frames > 0)
        rc = run_benchmark_mode(app);
    else if (app->cfg.wallpaper)
        rc = run_wallpaper_mode(app);
    else
        rc = run_tty_mode(app);

    sim_free(&app->sim);
    free(app->atlas.atlas_rg);
    free(app->grid_rgba);
    free(app->prev_packed);
    if (app->sw_surf) cairo_surface_destroy(app->sw_surf);
    free(app);
    return rc;
}
