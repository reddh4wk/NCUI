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

#ifdef _OPENMP
#include <omp.h>
#endif

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

#define NCMATRIX_VERSION "ncmatrix 1.0 (Nightcity high-perf Matrix engine)"
#define ATLAS_COLS 16
#define MAX_GLYPHS 256
#define PALETTE_SIZE 128
#define PALETTE_COLORS 63
#define BG_R 0.00f
#define BG_G 0.00f
#define BG_B 0.00f
#define BG_ARGB32 0xFF000000u
#define WP_BASE_TICK_MS 16

#define CELL_IS_HEAD(c)  (((c)->is_head & 0x01u) != 0u)
#define CELL_CS(c)       (((c)->is_head >> 1) & 0x07u)
#define CELL_RAINBOW(c)  (((c)->is_head & 0x10u) != 0u)
#define MAKE_CELL_META(hd, cs, rb) ((uint8_t)(((hd) ? 0x01u : 0u) | (((cs) & 7u) << 1) | ((rb) ? 0x10u : 0u)))

enum {
    CS_ASCII = 0,
    CS_KATAKANA = 1,
    CS_BINARY = 2,
    CS_HEX = 3,
    CS_LAMBDA = 4
};

enum {
    SENTINEL_EMPTY = 0xFFFFu,
    SENTINEL_HEAD_OLD = 0u,
    SENTINEL_PIPE_OLD = 1u
};

/* Cell layout: column-major per active stream (even columns j = k*2) */
typedef struct {
    uint16_t val;      /* Unicode codepoint, ' ', SENTINEL_EMPTY, 0, or 1 */
    uint8_t  is_head;
    uint8_t  color_id; /* 0..7 ncurses color index */
} MatrixCell;

typedef struct {
    int cols;       /* total screen columns (2 * n_active or 2 * n_active - 1) */
    int lines;      /* visible rows */
    int n_active;   /* (cols + 1) / 2 */
    int stride;     /* lines + 1 */
    int tick_count; /* 1..4 */
    uint32_t last_tty_cfg_tag;
    MatrixCell *cells;
    uint8_t    *col_dirty;
    uint8_t    *col_charset;
    uint8_t    *col_color;
    uint8_t    *col_speed;
    uint16_t   *col_accum;
    int        *length;
    int        *spaces;
    int        *updates;
    uint64_t   *rngs;
    bool        draining;
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
    int update;     /* cmatrix -u delay (0..20 -> delay*10ms) */
    int speed;      /* nc-ui speed (1..10, higher = faster) */
    int has_u;
    int has_speed;
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
    int      n_slots;     /* 1 + 2 * n_glyphs (normal then bold) */
    uint8_t *atlas_alpha; /* tight A8 buffer: atlas_w * atlas_h */
    uint16_t cp_to_slot[65536];
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
    bool        gl_ready;
    bool        gl_failed;
    uint8_t    *grid_rgba;
    int         grid_cap;

    /* Software & palette state */
    cairo_surface_t *sw_surf;
    uint32_t        *prev_packed;
    int              prev_cap;
    float            palette_rgb[PALETTE_SIZE][3];
    uint32_t         lut[PALETTE_SIZE][256];
    int              next_custom_slot;
    bool             palette_dirty;

    /* Runtime lifecycle */
    GtkWidget  *win;
    guint       timer_id;
    int         timer_interval_ms;
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
    return (uint32_t)((rng_next(s) >> 32) % mod);
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

static int hex_nibble(char c) {
    if (c >= '0' && c <= '9') return c - '0';
    if (c >= 'a' && c <= 'f') return 10 + (c - 'a');
    if (c >= 'A' && c <= 'F') return 10 + (c - 'A');
    return -1;
}

static bool parse_hex_rgb(const char *s, uint8_t rgb[3]) {
    if (!s) return false;
    if (s[0] == '#') s++;
    if (strlen(s) != 6) return false;
    for (int i = 0; i < 3; i++) {
        int hi = hex_nibble(s[i * 2]);
        int lo = hex_nibble(s[i * 2 + 1]);
        if (hi < 0 || lo < 0) return false;
        rgb[i] = (uint8_t)((hi << 4) | lo);
    }
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
    if (spd < 1) spd = 1;
    if (spd > 10) spd = 10;
    int ms = 117 - (int)lround(spd * 11.5);
    return (ms < WP_BASE_TICK_MS) ? WP_BASE_TICK_MS : ms;
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

static inline uint16_t pick_oldstyle_glyph_cs(int charset, const Config *cfg, uint64_t *rng, int *out_space) {
    int span = (charset == CS_KATAKANA) ? (0xFF9D - 0xFF66)
             : (charset == CS_BINARY)   ? 2
             : (charset == CS_HEX)      ? 16
             : (cfg->console || cfg->xwindow) ? 51 : 90;
    uint32_t r = rng_u32(rng, (uint32_t)(span + 8));
    if ((int)r >= span) {
        *out_space = 1;
        return ' ';
    }
    *out_space = 0;
    return pick_glyph_cs(charset, cfg, rng);
}

static void sim_free(MatrixSim *s) {
    free(s->cells);
    free(s->col_dirty);
    free(s->col_charset);
    free(s->col_color);
    free(s->col_speed);
    free(s->col_accum);
    free(s->length);
    free(s->spaces);
    free(s->updates);
    free(s->rngs);
    memset(s, 0, sizeof(*s));
}

static void sim_init(MatrixSim *s, int lines, int cols, const Config *cfg) {
    if (lines < 4) lines = 4;
    if (cols < 4) cols = 4;
    int n_active = (cols + 1) / 2;
    int stride = lines + 1;

    s->lines = lines;
    s->cols = cols;
    s->n_active = n_active;
    s->stride = stride;
    s->tick_count = 0;
    s->last_tty_cfg_tag = 0xFFFFFFFFu;
    s->draining = false;

    s->cells       = realloc(s->cells,       (size_t)n_active * (size_t)stride * sizeof(MatrixCell));
    s->col_dirty   = realloc(s->col_dirty,   (size_t)n_active * sizeof(uint8_t));
    s->col_charset = realloc(s->col_charset, (size_t)n_active * sizeof(uint8_t));
    s->col_color   = realloc(s->col_color,   (size_t)n_active * sizeof(uint8_t));
    s->col_speed   = realloc(s->col_speed,   (size_t)n_active * sizeof(uint8_t));
    s->col_accum   = realloc(s->col_accum,   (size_t)n_active * sizeof(uint16_t));
    s->length      = realloc(s->length,      (size_t)n_active * sizeof(int));
    s->spaces      = realloc(s->spaces,      (size_t)n_active * sizeof(int));
    s->updates     = realloc(s->updates,     (size_t)n_active * sizeof(int));
    s->rngs        = realloc(s->rngs,        (size_t)n_active * sizeof(uint64_t));

    if (!s->cells || !s->col_dirty || !s->col_charset || !s->col_color ||
        !s->col_speed || !s->col_accum || !s->length || !s->spaces ||
        !s->updates || !s->rngs) {
        fprintf(stderr, "ncmatrix: out of memory allocating grid (%dx%d)\n", cols, lines);
        exit(1);
    }
    memset(s->col_dirty, 1, (size_t)n_active * sizeof(uint8_t));

    uint8_t def_cs = (uint8_t)(cfg ? cfg->charset : CS_ASCII);
    uint8_t def_rb = (cfg && cfg->rainbow) ? 0xFFu : (uint8_t)((cfg && cfg->mcolor >= 0 && cfg->mcolor < PALETTE_COLORS) ? cfg->mcolor : COLOR_GREEN);
    uint8_t def_spd = (uint8_t)((cfg && cfg->speed > 0) ? cfg->speed : 4);
    uint8_t init_meta = MAKE_CELL_META(0, def_cs, def_rb == 0xFFu);
    uint8_t init_cid = (def_rb == 0xFFu) ? (uint8_t)((cfg->wallpaper || cfg->benchmark_frames > 0) ? 16 : COLOR_GREEN) : def_rb;

    uint64_t seed_base = ((uint64_t)time(NULL) << 32) ^ (uint64_t)getpid() ^ 0x9E3779B97F4A7C15ULL;
    for (int k = 0; k < n_active; k++) {
        uint64_t rng = seed_base ^ ((uint64_t)(k + 1) * 0xBF58476D1CE4E5B9ULL);
        rng_next(&rng);
        MatrixCell *col = &s->cells[(size_t)k * (size_t)stride];
        for (int i = 0; i <= lines; i++) {
            col[i].val = SENTINEL_EMPTY;
            col[i].is_head = init_meta;
            col[i].color_id = init_cid;
        }
        s->col_charset[k] = def_cs;
        s->col_color[k] = def_rb;
        s->col_speed[k] = def_spd;
        s->col_accum[k] = 0;
        s->spaces[k] = (int)rng_u32(&rng, (uint32_t)lines) + 1;
        s->length[k] = (lines > 3) ? ((int)rng_u32(&rng, (uint32_t)(lines - 3)) + 3) : 3;
        col[1].val = ' ';
        s->updates[k] = (int)rng_u32(&rng, 3u) + 1;
        s->rngs[k] = rng ? rng : 1ULL;
    }
}

static inline uint8_t pick_rainbow_cid(const Config *cfg, uint64_t *rng) {
    if (cfg->wallpaper || cfg->benchmark_frames > 0)
        return (uint8_t)(16u + rng_u32(rng, (uint32_t)(PALETTE_COLORS - 16)));
    return (uint8_t)k_rainbow_colors[rng_u32(rng, 6u)];
}

static inline void sim_step_column(MatrixSim *s, const Config *cfg, int k) {
    const int lines = s->lines;
    const int stride = s->stride;
    const int tick_count = s->tick_count;
    const int asynch = cfg->asynch;
    const int oldstyle = cfg->oldstyle;
    const int changes = cfg->changes;

    if (asynch && tick_count <= s->updates[k])
        return;
    s->col_dirty[k] = 1;

    uint64_t rng = s->rngs[k];
    MatrixCell *col = &s->cells[(size_t)k * (size_t)stride];
    uint8_t cs = s->col_charset[k];
    uint8_t col_spec = s->col_color[k];

    if (oldstyle) {
        for (int i = lines - 1; i >= 1; i--)
            col[i] = col[i - 1];

        int dummy_sp = 0;
        uint16_t cand = pick_oldstyle_glyph_cs(cs, cfg, &rng, &dummy_sp);

        if (col[1].val == SENTINEL_HEAD_OLD) {
            col[0].val = SENTINEL_PIPE_OLD;
            col[0].is_head = MAKE_CELL_META(0, CELL_CS(&col[1]), CELL_RAINBOW(&col[1]));
            col[0].color_id = col[1].color_id;
        } else if (col[1].val == ' ' || col[1].val == SENTINEL_EMPTY) {
            if (s->draining) {
                col[0].val = ' ';
                col[0].is_head = 0;
            } else if (s->spaces[k] > 0) {
                col[0].val = ' ';
                col[0].is_head = 0;
                s->spaces[k]--;
            } else {
                s->col_charset[k] = (uint8_t)cfg->charset;
                s->col_color[k]   = cfg->rainbow ? 0xFFu : (uint8_t)(cfg->mcolor % PALETTE_COLORS);
                s->col_speed[k]   = (uint8_t)(cfg->speed > 0 ? cfg->speed : 4);
                cs       = s->col_charset[k];
                col_spec = s->col_color[k];

                uint8_t hd = (rng_u32(&rng, 3u) == 1u) ? 1u : 0u;
                col[0].val = hd ? SENTINEL_HEAD_OLD : pick_glyph_cs(cs, cfg, &rng);
                col[0].is_head = MAKE_CELL_META(hd, cs, col_spec == 0xFFu);
                col[0].color_id = (col_spec == 0xFFu) ? pick_rainbow_cid(cfg, &rng) : col_spec;
                s->spaces[k] = (int)rng_u32(&rng, (uint32_t)lines) + 1;
            }
        } else if (dummy_sp && col[1].val != SENTINEL_PIPE_OLD) {
            col[0].val = ' ';
            col[0].is_head = 0;
        } else {
            uint8_t inherit_cs = CELL_CS(&col[1]);
            bool inherit_rb = CELL_RAINBOW(&col[1]);
            col[0].val = (cand == ' ') ? pick_glyph_cs(inherit_cs, cfg, &rng) : cand;
            col[0].is_head = MAKE_CELL_META(0, inherit_cs, inherit_rb);
            col[0].color_id = inherit_rb ? pick_rainbow_cid(cfg, &rng) : col[1].color_id;
        }
    } else {
        if (col[0].val == SENTINEL_EMPTY && col[1].val == ' ' && s->spaces[k] > 0) {
            if (!s->draining)
                s->spaces[k]--;
        } else if (col[0].val == SENTINEL_EMPTY && col[1].val == ' ') {
            if (!s->draining) {
                s->col_charset[k] = (uint8_t)cfg->charset;
                s->col_color[k]   = cfg->rainbow ? 0xFFu : (uint8_t)(cfg->mcolor % PALETTE_COLORS);
                s->col_speed[k]   = (uint8_t)(cfg->speed > 0 ? cfg->speed : 4);
                cs       = s->col_charset[k];
                col_spec = s->col_color[k];

                s->length[k] = (lines > 3) ? ((int)rng_u32(&rng, (uint32_t)(lines - 3)) + 3) : 3;
                col[0].val = pick_glyph_cs(cs, cfg, &rng);
                col[0].is_head = MAKE_CELL_META(0, cs, col_spec == 0xFFu);
                col[0].color_id = (col_spec == 0xFFu) ? pick_rainbow_cid(cfg, &rng) : col_spec;
                s->spaces[k] = (int)rng_u32(&rng, (uint32_t)lines) + 1;
            }
        }

        int i = 0;
        int firstcoldone = 0;
        while (i <= lines) {
            while (i <= lines && (col[i].val == ' ' || col[i].val == SENTINEL_EMPTY))
                i++;
            if (i > lines) break;

            int z = i;
            int y = 0;
            uint8_t seg_cs  = (z == 0) ? cs : CELL_CS(&col[z]);
            bool    seg_rb  = (z == 0) ? (col_spec == 0xFFu) : CELL_RAINBOW(&col[z]);
            uint8_t seg_cid = (z == 0) ? col[0].color_id : col[z].color_id;

            while (i <= lines && col[i].val != ' ' && col[i].val != SENTINEL_EMPTY) {
                col[i].is_head = MAKE_CELL_META(0, CELL_CS(&col[i]), CELL_RAINBOW(&col[i]));
                if (changes && rng_u32(&rng, 8u) == 0u)
                    col[i].val = pick_glyph_cs(CELL_CS(&col[i]), cfg, &rng);
                i++;
                y++;
            }
            if (i > lines) {
                col[z].val = ' ';
                col[z].is_head = 0;
                continue;
            }
            col[i].val = pick_glyph_cs(seg_cs, cfg, &rng);
            col[i].is_head = MAKE_CELL_META(1, seg_cs, seg_rb);
            col[i].color_id = seg_rb ? pick_rainbow_cid(cfg, &rng) : seg_cid;

            if (y > s->length[k] || firstcoldone) {
                col[z].val = ' ';
                col[z].is_head = 0;
                col[0].val = SENTINEL_EMPTY;
            }
            firstcoldone = 1;
            i++;
        }
    }
    s->rngs[k] = rng;
}

static bool sim_is_screen_empty(const MatrixSim *s, const Config *cfg) {
    int y_off = cfg->oldstyle ? 0 : 1;
    for (int k = 0; k < s->n_active; k++) {
        const MatrixCell *col = &s->cells[(size_t)k * (size_t)s->stride];
        for (int r = 0; r < s->lines; r++) {
            uint16_t v = col[r + y_off].val;
            if (v != ' ' && v != SENTINEL_EMPTY)
                return false;
        }
    }
    return true;
}

static void sim_step(MatrixSim *s, const Config *cfg) {
    if (cfg->paused) return;

    s->tick_count++;
    if (s->tick_count > 4) s->tick_count = 1;

    const int n_active = s->n_active;
    #pragma omp parallel for schedule(static) if(n_active >= 4096)
    for (int k = 0; k < n_active; k++)
        sim_step_column(s, cfg, k);
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
    memcpy(app->palette_rgb[0], k_base_palette_rgb[0], sizeof(float) * 3);
    memcpy(app->palette_rgb[1], k_base_palette_rgb[1], sizeof(float) * 3);
    for (int cid = 0; cid < 8; cid++) {
        memcpy(app->palette_rgb[2 + cid],  k_base_palette_rgb[2 + cid],  sizeof(float) * 3);
        memcpy(app->palette_rgb[65 + cid], k_base_palette_rgb[10 + cid], sizeof(float) * 3);
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

static void atlas_add_cp(GlyphAtlas *a, uint16_t cp) {
    if (cp == 0 || cp == ' ' || a->cp_to_slot[cp] != 0) return;
    if (a->n_glyphs + 1 >= MAX_GLYPHS) return;
    int idx = ++a->n_glyphs; /* 1-based */
    a->codepoints[idx] = cp;
    a->cp_to_slot[cp] = (uint16_t)idx;
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

static void atlas_build(GlyphAtlas *a, const Config *cfg, int scale) {
    free(a->atlas_alpha);
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

    cairo_surface_t *tmp = cairo_image_surface_create(CAIRO_FORMAT_A8, 64, 64);
    cairo_t *tmp_cr = cairo_create(tmp);
    PangoLayout *layout = pango_cairo_create_layout(tmp_cr);

    char font_norm_str[64], font_bold_str[64];
    int pt_scaled = fsize * scale;
    snprintf(font_norm_str, sizeof(font_norm_str), "monospace %d", pt_scaled);
    snprintf(font_bold_str, sizeof(font_bold_str), "monospace bold %d", pt_scaled);
    PangoFontDescription *desc_norm = pango_font_description_from_string(font_norm_str);
    PangoFontDescription *desc_bold = pango_font_description_from_string(font_bold_str);

    pango_layout_set_font_description(layout, desc_bold);
    PangoRectangle ink, log;
    int max_w = 0, max_h = 0;
    const char *probes[] = {"M", "W", "0", "\xEF\xBD\xB1", "\xEF\xBE\x9D", "\xCE\xBB"};
    for (size_t i = 0; i < sizeof(probes) / sizeof(probes[0]); i++) {
        pango_layout_set_text(layout, probes[i], -1);
        pango_layout_get_pixel_extents(layout, &ink, &log);
        if (log.width > max_w) max_w = log.width;
        if (ink.width > max_w) max_w = ink.width;
        if (log.height > max_h) max_h = log.height;
    }

    /* Even-column streams with odd-column gutters allow crisp, compact grid */
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

    PangoLayout *play = pango_cairo_create_layout(cr);
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
    a->atlas_alpha = calloc((size_t)a->atlas_w * (size_t)a->atlas_h, 1);
    if (!a->atlas_alpha) {
        fprintf(stderr, "ncmatrix: OOM allocating atlas\n");
        exit(1);
    }
    for (int y = 0; y < a->atlas_h; y++)
        memcpy(a->atlas_alpha + (size_t)y * (size_t)a->atlas_w, src + (size_t)y * (size_t)stride, (size_t)a->atlas_w);

    g_object_unref(play);
    cairo_destroy(cr);
    cairo_surface_destroy(surf);
    pango_font_description_free(desc_norm);
    pango_font_description_free(desc_bold);
    g_object_unref(layout);
    cairo_destroy(tmp_cr);
    cairo_surface_destroy(tmp);
}

/* Pack simulation state + optional -M message into cols * rows * 4 RGBA bytes */
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
    }

    int y_off = cfg->oldstyle ? 0 : 1;
    uint32_t *dst32 = (uint32_t *)(void *)app->grid_rgba;

    #pragma omp parallel for schedule(static) if(s->n_active >= 4096)
    for (int k = 0; k < s->n_active; k++) {
        if (!full_sync && !cfg->msg[0] && !s->col_dirty[k])
            continue;
        s->col_dirty[k] = 0;

        int j = k * 2;
        if (j >= s->cols) continue;
        const MatrixCell *col = &s->cells[(size_t)k * (size_t)s->stride];
        int r = 0;
        while (r < s->lines) {
            const MatrixCell *cr0 = &col[r + y_off];
            if (cr0->val == SENTINEL_EMPTY || cr0->val == ' ') {
                dst32[(size_t)r * (size_t)s->cols + (size_t)j] = 0u;
                r++;
                continue;
            }

            int r_top = r;
            while (r + 1 < s->lines &&
                   col[(r + 1) + y_off].val != SENTINEL_EMPTY &&
                   col[(r + 1) + y_off].val != ' ') {
                r++;
            }
            int r_bot = r;
            r++;

            const MatrixCell *c_bot = &col[r_bot + y_off];
            bool seg_has_head = (c_bot->val == SENTINEL_HEAD_OLD) || CELL_IS_HEAD(c_bot);
            int seg_len = r_bot - r_top + 1;
            int tail_len = s->length[k];
            int span = ((r_top == 0 && seg_has_head) || !seg_has_head)
                     ? ((seg_len > tail_len) ? seg_len : tail_len)
                     : seg_len;
            if (span < 2) span = 2;

            for (int rr = r_top; rr <= r_bot; rr++) {
                const MatrixCell *c = &col[rr + y_off];
                uint16_t v = c->val;
                size_t cell_idx = (size_t)rr * (size_t)s->cols + (size_t)j;

                uint16_t disp_cp = v;
                bool is_bold = false;
                bool is_hd = CELL_IS_HEAD(c);
                bool cell_rb = CELL_RAINBOW(c);
                uint8_t style = 0;

                if (v == SENTINEL_HEAD_OLD || (is_hd && !cell_rb)) {
                    disp_cp = (v == SENTINEL_HEAD_OLD) ? '&' : v;
                    is_bold = (cfg->bold != -1);
                    style = 1;
                } else {
                    uint8_t cid = (uint8_t)(c->color_id % PALETTE_COLORS);
                    if (v == SENTINEL_PIPE_OLD) {
                        disp_cp = '|';
                        is_bold = (cfg->bold > 0);
                    } else {
                        disp_cp = v;
                        is_bold = (cfg->bold == 2) || (cfg->bold == 1 && (v & 1u) == 0u);
                    }
                    style = (uint8_t)((is_bold ? 65 : 2) + cid);
                }

                uint16_t base_slot = a->cp_to_slot[disp_cp];
                if (base_slot == 0) {
                    base_slot = a->cp_to_slot['?'];
                    if (base_slot == 0 && a->n_glyphs > 0) base_slot = 1;
                }
                if (base_slot == 0) {
                    dst32[cell_idx] = 0u;
                    continue;
                }
                uint16_t slot = (uint16_t)(base_slot + (is_bold ? a->n_glyphs : 0));

                int dist = seg_has_head ? (r_bot - rr) : (span - (rr - r_top));
                if (dist < 0) dist = 0;
                uint8_t fade_u8;
                if (!cfg->fade || v == SENTINEL_HEAD_OLD || (is_hd && !cell_rb) || (dist == 0 && seg_has_head)) {
                    fade_u8 = 255u;
                } else {
                    float ratio = 1.0f - ((float)dist / (float)(span + 1));
                    if (ratio < 0.14f) ratio = 0.14f;
                    long fv = lroundf(ratio * 0.90f * 255.0f);
                    fade_u8 = (fv > 255L) ? 255u : (fv < 0L ? 0u : (uint8_t)fv);
                }

                uint8_t *dst = &app->grid_rgba[cell_idx * 4u];
                dst[0] = (uint8_t)(slot & 0xFFu);
                dst[1] = style;
                dst[2] = (uint8_t)(slot >> 8);
                dst[3] = fade_u8;
            }
        }
    }

    if (cfg->msg[0]) {
        uint8_t *dst = app->grid_rgba;
        int mlen = (int)strlen(cfg->msg);
        int mr = s->lines / 2;
        int mc = s->cols / 2 - mlen / 2;
        uint8_t mstyle = (uint8_t)(65 + (cfg->mcolor % PALETTE_COLORS));
        for (int dr = -1; dr <= 1; dr++) {
            int rr = mr + dr;
            if (rr < 0 || rr >= s->lines) continue;
            for (int dc = -2; dc < mlen + 2; dc++) {
                int cc = mc + dc;
                if (cc < 0 || cc >= s->cols) continue;
                size_t out_idx = ((size_t)rr * (size_t)s->cols + (size_t)cc) * 4u;
                if (dr == 0 && dc >= 0 && dc < mlen) {
                    uint8_t ch = (uint8_t)cfg->msg[dc];
                    uint16_t base_slot = a->cp_to_slot[ch];
                    if (ch == ' ' || base_slot == 0) {
                        dst[out_idx + 0] = dst[out_idx + 1] = dst[out_idx + 2] = 0;
                    } else {
                        uint16_t slot = (uint16_t)(base_slot + a->n_glyphs);
                        dst[out_idx + 0] = (uint8_t)(slot & 0xFFu);
                        dst[out_idx + 1] = mstyle;
                        dst[out_idx + 2] = (uint8_t)(slot >> 8);
                        dst[out_idx + 3] = 255u;
                    }
                } else {
                    dst[out_idx + 0] = dst[out_idx + 1] = dst[out_idx + 2] = 0;
                }
            }
        }
    }
}

/* Software incremental dirty-cell renderer (used for --software / GL fallback / software bench) */
static void sync_sw_surface(WallpaperApp *app, bool queue_dirty_regions) {
    const MatrixSim *s = &app->sim;
    const GlyphAtlas *a = &app->atlas;
    if (!app->sw_surf || !app->grid_rgba) return;

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
    const int c_step = app->cfg.msg[0] ? 1 : 2;
    bool any_dirty = false;
    int min_x = surf_w, min_y = surf_h, max_x = 0, max_y = 0;

    for (int r = 0; r < s->lines; r++) {
        int py0 = r * ch;
        if (py0 >= surf_h) break;
        int draw_h = (py0 + ch <= surf_h) ? ch : (surf_h - py0);

        for (int c = 0; c < s->cols; c += c_step) {
            int idx = r * s->cols + c;
            const uint8_t *g = &app->grid_rgba[(size_t)idx * 4u];
            uint32_t packed = (uint32_t)g[0] | ((uint32_t)g[1] << 8) | ((uint32_t)g[2] << 16) | ((uint32_t)g[3] << 24);
            if (packed == app->prev_packed[idx])
                continue;
            app->prev_packed[idx] = packed;

            int px0 = c * cw;
            if (px0 >= surf_w) continue;
            int draw_w = (px0 + cw <= surf_w) ? cw : (surf_w - px0);

            uint16_t slot = (uint16_t)g[0] | ((uint16_t)g[2] << 8);
            uint8_t style = g[1];
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
                for (int dy = 0; dy < draw_h; dy++) {
                    const uint8_t *src_a = a->atlas_alpha + (size_t)(ay0 + dy) * (size_t)aw + (size_t)ax0;
                    uint32_t *row_p = pixels + (size_t)(py0 + dy) * (size_t)sw_stride + (size_t)px0;
                    for (int dx = 0; dx < draw_w; dx++)
                        row_p[dx] = lut_row[(src_a[dx] * fade) >> 8];
                }
            }

            any_dirty = true;
            if (px0 < min_x) min_x = px0;
            if (py0 < min_y) min_y = py0;
            if (px0 + draw_w > max_x) max_x = px0 + draw_w;
            if (py0 + draw_h > max_y) max_y = py0 + draw_h;
        }
    }
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
        fprintf(stderr, "ncmatrix GL shader error: %s\n", log);
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

    if (app->atlas.atlas_alpha == NULL || app->atlas.scale != scale || app->atlas.font_size != req_fs) {
        atlas_build(&app->atlas, &app->cfg, scale);
        app->prev_cap = 0;
        free(app->prev_packed);
        app->prev_packed = NULL;
    }

    int cols = phys_w / app->atlas.cell_w;
    int rows = phys_h / app->atlas.cell_h;
    if (cols < 4) cols = 4;
    if (rows < 4) rows = 4;

    if (force_reinit || cols != app->sim.cols || rows != app->sim.lines) {
        sim_init(&app->sim, rows, cols, &app->cfg);
        if (force_reinit) {
            uint32_t span = (uint32_t)(rows / 4 > 1 ? rows / 4 : 2);
            for (int k = 0; k < app->sim.n_active; k++)
                app->sim.spaces[k] = (int)rng_u32(&app->sim.rngs[k], span) + 1;
        }
        if (app->grid_rgba && app->grid_cap > 0)
            memset(app->grid_rgba, 0, (size_t)app->grid_cap * 4u);
        sync_grid_rgba(app);
        app->prev_cap = 0;
        free(app->prev_packed);
        app->prev_packed = NULL;
    }
}

static void upload_atlas_tex(WallpaperApp *app) {
    if (!app->atlas.atlas_alpha) return;
    glBindTexture(GL_TEXTURE_2D, app->tex_atlas);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_R8, app->atlas.atlas_w, app->atlas.atlas_h, 0,
                 GL_RED, GL_UNSIGNED_BYTE, app->atlas.atlas_alpha);
}

static void upload_grid_alloc_tex(WallpaperApp *app) {
    glBindTexture(GL_TEXTURE_2D, app->tex_grid);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_NEAREST);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
    glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA8UI, app->sim.cols, app->sim.lines, 0,
                 GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, app->grid_rgba);
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
    fprintf(stderr, "ncmatrix: %s, falling back to software renderer\n",
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
    glViewport(0, 0, app->egl_w, app->egl_h);

    glUseProgram(app->prog);
    glBindVertexArray(app->vao);

    glActiveTexture(GL_TEXTURE0);
    glBindTexture(GL_TEXTURE_2D, app->tex_atlas);

    glActiveTexture(GL_TEXTURE1);
    glBindTexture(GL_TEXTURE_2D, app->tex_grid);
    glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
    glTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, app->sim.cols, app->sim.lines,
                    GL_RGBA_INTEGER, GL_UNSIGNED_BYTE, app->grid_rgba);

    glUniform2i(app->u_view_size, app->egl_w, app->egl_h);
    glUniform2i(app->u_cell_size, app->atlas.cell_w, app->atlas.cell_h);
    glUniform2i(app->u_grid_size, app->sim.cols, app->sim.lines);

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
        "    uvec4 cell = texelFetch(u_grid, ivec2(col, row), 0);\n"
        "    uint slot = cell.r + (cell.b << 8u);\n"
        "    uint style = cell.g;\n"
        "    if (slot == 0u || style == 0u) {\n"
        "        fragColor = vec4(u_bg, 1.0);\n"
        "        return;\n"
        "    }\n"
        "    int lx = px - col * u_cell_size.x;\n"
        "    int ly = py - row * u_cell_size.y;\n"
        "    int acol = int(slot) & 15;\n"
        "    int arow = int(slot) >> 4;\n"
        "    float a = texelFetch(u_atlas, ivec2(acol * u_cell_size.x + lx, arow * u_cell_size.y + ly), 0).r * (float(cell.a) * (1.0 / 255.0));\n"
        "    vec3 fg = u_palette[min(int(style), 127)];\n"
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

    glUseProgram(prog);
    glUniform1i(glGetUniformLocation(prog, "u_atlas"), 0);
    glUniform1i(glGetUniformLocation(prog, "u_grid"), 1);
    glUniform3fv(app->u_palette, PALETTE_SIZE, &app->palette_rgb[0][0]);
    app->palette_dirty = false;
    glUniform3f(app->u_bg, BG_R, BG_G, BG_B);

    glGenVertexArrays(1, &app->vao);
    glBindVertexArray(app->vao);
    glGenTextures(1, &app->tex_atlas);
    glGenTextures(1, &app->tex_grid);

    resize_grid_for_pixels(app, phys_w, phys_h, scale, false);
    upload_atlas_tex(app);
    upload_grid_alloc_tex(app);
    app->gl_ready = true;

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

    if (eglGetCurrentContext() != app->egl_ctx)
        eglMakeCurrent(app->egl_dpy, app->egl_surf, app->egl_surf, app->egl_ctx);

    if (app->palette_dirty) {
        glUseProgram(app->prog);
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
    wp_reschedule_timer(app, speed_to_ms(app->cfg.speed));
}

static gboolean on_wallpaper_tick(gpointer user_data) {
    WallpaperApp *app = (WallpaperApp *)user_data;
    if (!app->running) return G_SOURCE_REMOVE;
    if (app->cfg.paused) return G_SOURCE_CONTINUE;

    bool any_stepped = false;
    bool any_mixed = false;
    int target_ms = speed_to_ms(app->cfg.speed);
    uint8_t def_spd = (uint8_t)(app->cfg.speed > 0 ? app->cfg.speed : 4);

    if (!app->sim.draining && app->timer_interval_ms == target_ms) {
        bool uniform = true;
        for (int k = 0; k < app->sim.n_active; k++) {
            if (app->sim.col_speed[k] != def_spd) {
                uniform = false;
                break;
            }
        }
        if (uniform) {
            sim_step(&app->sim, &app->cfg);
            any_stepped = true;
        } else {
            wp_reschedule_timer(app, WP_BASE_TICK_MS);
        }
    }

    if (!any_stepped) {
        app->sim.tick_count++;
        if (app->sim.tick_count > 4) app->sim.tick_count = 1;
        int step_dt = (app->timer_interval_ms > 0) ? app->timer_interval_ms : WP_BASE_TICK_MS;
        for (int k = 0; k < app->sim.n_active; k++) {
            uint8_t cspd = app->sim.col_speed[k];
            if (cspd != def_spd) any_mixed = true;
            int c_ms = speed_to_ms(cspd);
            if (app->sim.draining && c_ms > 28) c_ms = 28;
            app->sim.col_accum[k] = (uint16_t)(app->sim.col_accum[k] + step_dt);
            if ((int)app->sim.col_accum[k] >= c_ms) {
                app->sim.col_accum[k] = (uint16_t)(app->sim.col_accum[k] % c_ms);
                sim_step_column(&app->sim, &app->cfg, k);
                any_stepped = true;
            }
        }
        if (!app->sim.draining && !any_mixed && app->timer_interval_ms != target_ms)
            wp_reschedule_timer(app, target_ms);
    }

    if (!any_stepped)
        return G_SOURCE_CONTINUE;

    sync_grid_rgba(app);
    if (!app->cfg.software && !app->gl_failed && app->gl_ready)
        render_egl_frame(app);
    else if (app->sw_surf)
        sync_sw_surface(app, true);
    else if (app->sw_area)
        gtk_widget_queue_draw(app->sw_area);

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
    snprintf(path, sizeof(path), "/tmp/ncmatrix_%s.conf",
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
            if (sp >= 1 && sp <= 10) {
                next.speed = sp;
                next.has_speed = 1;
            }
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

    if (req_drain || next.font_size != app->cfg.font_size) {
        app->pending_cfg = next;
        app->pending_cfg.paused = 0;
        app->has_pending_cfg = true;
        app->sim.draining = true;
        app->cfg.paused = 0;
        wp_reschedule_timer(app, 28);
    } else {
        app->sim.draining = false;
        app->has_pending_cfg = false;
        app->cfg.charset   = next.charset;
        app->cfg.mcolor    = next.mcolor;
        app->cfg.rainbow   = next.rainbow;
        app->cfg.speed     = next.speed;
        app->cfg.has_speed = next.has_speed;
        app->cfg.bold      = next.bold;
        app->cfg.asynch    = next.asynch;
        app->cfg.fade      = next.fade;
        app->cfg.paused    = 0;
        wp_reschedule_timer(app, WP_BASE_TICK_MS);
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

static int compute_tick_ms(const Config *cfg) {
    if (cfg->has_u || (!cfg->has_speed && !cfg->wallpaper && cfg->benchmark_frames == 0)) {
        int d = cfg->update * 10;
        return (d < 10) ? 10 : d;
    }
    int spd = cfg->speed;
    if (spd < 1) spd = 1;
    if (spd > 10) spd = 10;
    int ms = 117 - (int)lround(spd * 11.5);
    return (ms < 16) ? 16 : ms;
}

static int run_wallpaper_mode(WallpaperApp *app) {
    ensure_wayland_env();
    if (!gtk_init_check(NULL, NULL)) {
        fprintf(stderr, "ncmatrix: failed to initialize GTK3/Wayland display\n");
        return 1;
    }

    init_app_palette(app);
    app->running = true;

    GtkWidget *win = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    app->win = win;
    char title_buf[96];
    snprintf(title_buf, sizeof(title_buf), "ncmatrix-%s", app->cfg.output_name[0] ? app->cfg.output_name : "bg");
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

    int tick_ms = compute_tick_ms(&app->cfg);
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

    for (int i = 0; i < 300; i++)
        sim_step(&app->sim, &app->cfg);

    struct timespec t0, t1;
    clock_gettime(CLOCK_MONOTONIC, &t0);
    for (int i = 0; i < frames; i++) {
        sim_step(&app->sim, &app->cfg);
        sync_grid_rgba(app);
        if (app->cfg.software)
            sync_sw_surface(app, false);
    }
    clock_gettime(CLOCK_MONOTONIC, &t1);

    double elapsed_ms = ((double)(t1.tv_sec - t0.tv_sec) * 1000.0) +
                        ((double)(t1.tv_nsec - t0.tv_nsec) / 1e6);
    double ms_per_frame = elapsed_ms / (double)frames;
    int tick_ms = compute_tick_ms(&app->cfg);
    double target_fps = 1000.0 / (double)tick_ms;
    double est_cpu_pct = (ms_per_frame / (double)tick_ms) * 100.0;

    printf("ncmatrix benchmark (%s path):\n", app->cfg.software ? "software-dirty-tile" : "gpu-grid-pack");
    printf("  resolution : %dx%d @ %dpt (cell %dx%d px)\n", w, h, app->cfg.font_size, app->atlas.cell_w, app->atlas.cell_h);
    printf("  grid       : %dx%d (%d active streams, %d glyph slots)\n",
           cols, rows, app->sim.n_active, app->atlas.n_slots);
    printf("  frames     : %d in %.2f ms -> %.4f ms/frame (max %.0f FPS)\n",
           frames, elapsed_ms, ms_per_frame, 1000.0 / (ms_per_frame > 1e-6 ? ms_per_frame : 1e-6));
    printf("  target     : speed=%d (~%.1f FPS / %d ms) -> est. %.2f%% of 1 core\n",
           app->cfg.speed, target_fps, tick_ms, est_cpu_pct);
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
        if (system("setfont >/dev/null 2>&1") != 0)
            (void)system("consolechars -d >/dev/null 2>&1");
    }
}

static void tty_resize(MatrixSim *sim, Config *cfg, int tty_fd,
                       uint32_t **prev_buf, size_t *prev_cap,
                       char **tty_buf, size_t *tty_cap) {
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
        uint32_t *np = realloc(*prev_buf, total * sizeof(uint32_t));
        if (!np) exit(1);
        *prev_buf = np;
        *prev_cap = total;
    }
    memset(*prev_buf, 0xFF, total * sizeof(uint32_t));

    size_t need_cap = total * 16u + 4096u;
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

static void tty_render_dirty(MatrixSim *s, const Config *cfg, int tty_fd,
                             uint32_t *prev_buf, char *tty_buf,
                             bool has_col, bool default_bg) {
    int y_off = cfg->oldstyle ? 0 : 1;
    int max_r = cfg->oldstyle ? (LINES - 1) : LINES;
    if (max_r > s->lines) max_r = s->lines;

    uint32_t cfg_tag = ((uint32_t)(cfg->mcolor & 7) << 25)
                     | ((uint32_t)((cfg->bold + 1) & 3) << 28)
                     | ((uint32_t)(cfg->rainbow & 1) << 23)
                     | ((uint32_t)(cfg->charset & 7) << 20)
                     | ((uint32_t)(cfg->console & 1) << 24);
    if (cfg->xwindow) cfg_tag ^= (1u << 24);
    if (cfg->oldstyle) cfg_tag ^= (1u << 22);
    bool cfg_changed = (cfg_tag != s->last_tty_cfg_tag);
    s->last_tty_cfg_tag = cfg_tag;

    char *p = tty_buf;
    memcpy(p, "\x1b[?2026h", 8);
    p += 8;

    int cur_r = -1;
    int cur_c = -1;
    int cur_pair = -1;
    int cur_bold = -1;
    int cur_alt = 0;
    bool emitted = false;
    const size_t stride_lines = (size_t)LINES;

    for (int k = 0; k < s->n_active; k++) {
        if (!s->col_dirty[k] && !cfg_changed) continue;
        s->col_dirty[k] = 0;

        int j = k * 2;
        if (j >= COLS || j >= s->cols) continue;
        const MatrixCell *col = &s->cells[(size_t)k * (size_t)s->stride];
        size_t col_base = (size_t)j * stride_lines;

        for (int r = 0; r < max_r; r++) {
            const MatrixCell *c = &col[r + y_off];
            uint16_t v = c->val;
            uint32_t raw_sig = (v == SENTINEL_EMPTY || v == ' ')
                ? 0x0020u
                : ((uint32_t)v | ((uint32_t)CELL_IS_HEAD(c) << 16) | (((uint32_t)c->color_id & 7u) << 17) | cfg_tag);
            size_t idx = col_base + (size_t)r;
            if (prev_buf[idx] == raw_sig) continue;
            prev_buf[idx] = raw_sig;

            wchar_t wc = L' ';
            int pair = cfg->mcolor;
            int is_bold = 0;
            int is_alt = (cfg->console || cfg->xwindow) ? 1 : 0;

            if (v == SENTINEL_EMPTY || v == ' ') {
                wc = L' ';
                pair = cfg->mcolor;
                is_bold = 0;
                is_alt = 0;
            } else if (v == SENTINEL_HEAD_OLD || (CELL_IS_HEAD(c) && !cfg->rainbow)) {
                pair = COLOR_WHITE;
                is_bold = (cfg->bold > 0) ? 1 : 0;
                wc = (v == SENTINEL_HEAD_OLD)
                   ? ((cfg->console || cfg->xwindow) ? 183 : L'&')
                   : ((cfg->charset == CS_LAMBDA) ? (wchar_t)0x03BB : (wchar_t)v);
            } else if (v == SENTINEL_PIPE_OLD) {
                pair = cfg->rainbow ? (c->color_id & 7) : cfg->mcolor;
                is_bold = (cfg->bold > 0) ? 1 : 0;
                is_alt = 0;
                wc = L'|';
            } else {
                pair = cfg->rainbow ? (c->color_id & 7) : cfg->mcolor;
                is_bold = (cfg->bold == 2 || (cfg->bold == 1 && (v % 2 == 0))) ? 1 : 0;
                wc = (cfg->charset == CS_LAMBDA) ? (wchar_t)0x03BB : (wchar_t)v;
            }

            if (r != cur_r || j != cur_c)
                p = emit_cup(p, r + 1, j + 1);
            if (pair != cur_pair || is_bold != cur_bold || is_alt != cur_alt) {
                p = emit_sgr(p, pair, is_bold, is_alt, &cur_alt, has_col, default_bg);
                cur_pair = pair;
                cur_bold = is_bold;
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
                    prev_buf[(size_t)cc * stride_lines + (size_t)rr] != 0xFFFFFFFEu) {
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
            p = emit_sgr(p, cfg->mcolor, 1, 0, &cur_alt, has_col, default_bg);
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
                    prev_buf[(size_t)cc * stride_lines + (size_t)rr] = 0xFFFFFFFEu;
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
            fprintf(stderr, "ncmatrix: error: '%s' couldn't be opened: %s.\n", cfg->tty, strerror(errno));
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
            fprintf(stderr, "ncmatrix: failed to load matrix console font.\n");
            return 1;
        }
    }

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
    uint32_t *prev_buf = malloc(prev_cap * sizeof(uint32_t));
    size_t tty_cap = prev_cap * 16u + 4096u;
    char *tty_buf = malloc(tty_cap);
    if (!prev_buf || !tty_buf) {
        fprintf(stderr, "ncmatrix: out of memory allocating TTY buffers\n");
        free(prev_buf);
        free(tty_buf);
        tty_finish(cfg, tty_fd);
        if (scr) delscreen(scr);
        if (ftty) fclose(ftty);
        return EXIT_FAILURE;
    }
    memset(prev_buf, 0xFF, prev_cap * sizeof(uint32_t));
    sim_init(&app->sim, LINES, COLS, cfg);

    for (;;) {
        struct timespec t_start, t_end;
        clock_gettime(CLOCK_MONOTONIC, &t_start);

        if ((g_tty_sig == SIGINT || g_tty_sig == SIGQUIT || g_tty_sig == SIGTSTP) && cfg->lock != 1)
            break;
        if (g_tty_sig == SIGWINCH) {
            g_tty_sig = 0;
            tty_resize(&app->sim, cfg, tty_fd, &prev_buf, &prev_cap, &tty_buf, &tty_cap);
        }

        int kp = wgetch(stdscr);
        if (kp != ERR) {
            if (cfg->screensaver) break;
            switch (kp) {
            case 'q': if (cfg->lock != 1) goto done; break;
            case 'a': cfg->asynch = !cfg->asynch; break;
            case 'b': cfg->bold = 1; break;
            case 'B': cfg->bold = 2; break;
            case 'L': cfg->lock = 1; break;
            case 'n': cfg->bold = 0; break;
            case '0' ... '9': cfg->update = kp - '0'; cfg->has_u = 1; cfg->has_speed = 0; break;
            case '!': cfg->mcolor = COLOR_RED;     cfg->rainbow = 0; break;
            case '@': cfg->mcolor = COLOR_GREEN;   cfg->rainbow = 0; break;
            case '#': cfg->mcolor = COLOR_YELLOW;  cfg->rainbow = 0; break;
            case '$': cfg->mcolor = COLOR_BLUE;    cfg->rainbow = 0; break;
            case '%': cfg->mcolor = COLOR_MAGENTA; cfg->rainbow = 0; break;
            case '^': cfg->mcolor = COLOR_CYAN;    cfg->rainbow = 0; break;
            case '&': cfg->mcolor = COLOR_WHITE;   cfg->rainbow = 0; break;
            case 'r': cfg->rainbow = !cfg->rainbow; break;
            case 'm': cfg->charset = (cfg->charset == CS_LAMBDA) ? CS_ASCII : CS_LAMBDA; break;
            case 'p':
            case 'P': cfg->paused = !cfg->paused; break;
            default: break;
            }
        }

        sim_step(&app->sim, cfg);
        tty_render_dirty(&app->sim, cfg, tty_fd, prev_buf, tty_buf, has_col, default_bg);

        int target_ms = compute_tick_ms(cfg);
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
    printf(" Usage: ncmatrix -[abBcfhlLonrsmkxV] [-u delay] [-C color] [-t tty] [-M message]\n");
    printf("   ncmatrix flags:\n");
    printf("     -a: Asynchronous scroll         -b: Bold characters on\n");
    printf("     -B: All bold characters           -n: No bold characters\n");
    printf("     -c: Japanese Katakana charset     -o: Old-style scrolling\n");
    printf("     -s: Screensaver mode              -l: Linux console font\n");
    printf("     -L: Lock mode                     -x: X window mtx.pcf mode\n");
    printf("     -r: Rainbow mode                  -m: Lambda mode\n");
    printf("     -k: Mutate chars while scrolling  -M [msg]: Centered message\n");
    printf("     -u delay (0-10, default 4)        -C [color]: Matrix color\n");
    printf("     -t [tty]: Target tty              -V: Version   -h: Help\n");
    printf("   Wayland wallpaper & high-performance options:\n");
    printf("     --wallpaper                       Run as GtkLayerShell desktop wallpaper\n");
    printf("     --output <NAME>                   Target Wayland output (e.g. DP-1)\n");
    printf("     --charset <katakana|ascii|binary|hex|lambda>\n");
    printf("     --color <color|rainbow>           --speed <1..10> (higher = faster)\n");
    printf("     --font-size <5..32>               Glyph size in pt (default 9)\n");
    printf("     --x <X> --y <Y>                   Monitor logical origin\n");
    printf("     --width <W> --height <H>          Monitor dimensions\n");
    printf("     --pid-file <PATH>                 Runtime PID file\n");
    printf("     --ipc-socket <PATH>               Control socket (quit/pause/resume)\n");
    printf("     --software                        Force CPU dirty-tile Cairo renderer\n");
    printf("     --benchmark <FRAMES>              Headless performance benchmark\n");
}

enum {
    OPT_WALLPAPER = 1000,
    OPT_OUTPUT,
    OPT_CHARSET,
    OPT_COLOR_LONG,
    OPT_SPEED,
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
    app->cfg.speed = 4;
    app->cfg.fade = 1;
    app->cfg.mcolor = COLOR_GREEN;
    app->cfg.font_size = 9;
    app->cfg.charset = CS_ASCII;

    static const struct option long_opts[] = {
        {"wallpaper",  no_argument,       NULL, OPT_WALLPAPER},
        {"output",     required_argument, NULL, OPT_OUTPUT},
        {"charset",    required_argument, NULL, OPT_CHARSET},
        {"color",      required_argument, NULL, OPT_COLOR_LONG},
        {"speed",      required_argument, NULL, OPT_SPEED},
        {"font-size",  required_argument, NULL, OPT_FONT_SIZE},
        {"width",      required_argument, NULL, OPT_WIDTH},
        {"height",     required_argument, NULL, OPT_HEIGHT},
        {"x",          required_argument, NULL, OPT_POS_X},
        {"y",          required_argument, NULL, OPT_POS_Y},
        {"pid-file",   required_argument, NULL, OPT_PID_FILE},
        {"ipc-socket", required_argument, NULL, OPT_IPC_SOCKET},
        {"software",   no_argument,       NULL, OPT_SOFTWARE},
        {"benchmark",  required_argument, NULL, OPT_BENCHMARK},
        {"version",    no_argument,       NULL, 'V'},
        {"help",       no_argument,       NULL, 'h'},
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
                        fprintf(stderr, "ncmatrix: invalid color '%s'\n", optarg);
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
        case OPT_OUTPUT:     snprintf(app->cfg.output_name, sizeof(app->cfg.output_name), "%s", optarg); break;
        case OPT_CHARSET:    app->cfg.charset = charset_from_name(optarg); break;
        case OPT_SPEED:
            app->cfg.speed = atoi(optarg);
            app->cfg.has_speed = 1;
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
    free(app->atlas.atlas_alpha);
    free(app->grid_rgba);
    free(app->prev_packed);
    if (app->sw_surf) cairo_surface_destroy(app->sw_surf);
    free(app);
    return rc;
}
