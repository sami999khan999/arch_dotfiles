/* bartips.c — the bar's tooltips: our own card under the hovered module instead of GTK's tooltip.
 *
 * A waybar CFFI module ("cffi/bartips" in config.jsonc, an empty zero-width widget) loaded into each
 * waybar. It watches every widget's enter / leave / click / scroll through GLib emission hooks (one
 * hook covers every module, tray icons that come later too), stops GTK's own tooltip on whatever has
 * one, and shows a layer-shell card under the module (namespace "bartip": blurred and faded in / out
 * by windowrules.lua) built from the module's tooltip markup. The pointer can move onto the card: it
 * stays while hovered (the calendar scrolls there too) and hides once the pointer is off both.
 *
 *   "<b>Title</b>  <span alpha='55%'>Super + I</span>"   a row: title, then the keys as key caps
 *   a first line                                          the title
 *   "60 fps  <span alpha='55%'>while moving</span>"      a value: a two-column list, what | value
 *   a line saying what a click / scroll does              hints, under a rule: key caps + what
 *   anything else                                         the body
 *
 * The clock (#custom-clock) has no tooltip of its own: the card is a month calendar (ISO weeks,
 * today, the days around the month), scrolled a month at a time over the clock.
 *
 * The hub (#custom-hub, first in "group/hub") is one icon for the group's other modules: they're
 * taken off the bar into an offscreen window, where waybar keeps updating them, and its card is a menu
 * of them (their icon, tooltip title, state and keys), opened by hovering or clicking the icon. A row
 * hands its click / right-click / scroll to its module, so it does exactly what that icon did.
 * A tooltip that is JSON ({"bartip": 1, "blocks": […]}) is a rich card: meters, a memory stick, a GPU
 * die, tables (cards.c; the scripts storage.py, memory.py, gpu.py make them).
 * Colours and sizes are in style.css (.bartip-*), so the colour themes reach it too.
 *
 * Built by scripts/waybars.py before it starts the bars, whenever this file is newer than the .so
 * (gcc, gtk3, gtk-layer-shell, json-glib). Nothing runs while the pointer is off the bar: no timers but the
 * show / hide delays.
 */
#include <gtk/gtk.h>
#include <gtk-layer-shell/gtk-layer-shell.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

const size_t wbcffi_version = 2;

#define SHOW_DELAY 300   /* ms over a module before the first card; moving on to the next is instant */
#define HIDE_DELAY 200   /* ms after leaving, so crossing to a neighbour, or down onto the card, doesn't flicker */
#define WARM 500         /* ms after a card hid during which the next one shows at once */

static GtkWidget *popup, *card;
static GtkWidget *anchor;    /* the module's event box under the pointer (weak) */
static GtkWidget *source;    /* the widget whose tooltip the card shows (weak) */
static gulong source_notify;
enum { TIP, CLOCK, HUB };
static int kind;             /* what the card shows */
static gboolean clicked;
static GtkWidget *holder;    /* the hub's modules, off the bar: realized and updating, never shown */
static GPtrArray *hub_items; /* their event boxes, in the bar's order */
static int month_offset;
static double scroll_sum;
static guint show_timer, hide_timer;
static gint64 hidden_at;
static GtkWidget *kept_for;  /* the module whose card's size is being kept (weak) */
static int keep_w, keep_h;   /* its largest size so far: a live card grows but never shrinks */
static GRegex *row_re, *hint_re, *keys_re, *kv_re, *space_re, *pct_re;

/* ---- GTK's own tooltip: stopped on every widget of the bar ----------------------------------- */

static gboolean no_gtk_tooltip(GtkWidget *w, gint x, gint y, gboolean kb, GtkTooltip *t, gpointer d) {
    g_signal_stop_emission_by_name(w, "query-tooltip");
    return FALSE;
}

static void stop_tooltips(GtkWidget *w) {
    if (!g_object_get_data(G_OBJECT(w), "bartips")) {
        g_object_set_data(G_OBJECT(w), "bartips", GINT_TO_POINTER(1));
        g_signal_connect(w, "query-tooltip", G_CALLBACK(no_gtk_tooltip), NULL);
    }
    if (GTK_IS_CONTAINER(w))
        gtk_container_forall(GTK_CONTAINER(w), (GtkCallback)stop_tooltips, NULL);
}

/* ---- what's under the pointer ------------------------------------------------------------------- */

static gboolean has_tip(GtkWidget *w) {
    gchar *m = gtk_widget_get_has_tooltip(w) ? gtk_widget_get_tooltip_markup(w) : NULL;
    gboolean yes = m && *m;
    g_free(m);
    return yes;
}

/* The widget whose tooltip belongs to this event box: itself, or the first inside it, not looking
   into another module's event box (a group's box holds several). */
static GtkWidget *find_tip(GtkWidget *w, gboolean top) {
    if (!top && GTK_IS_EVENT_BOX(w)) return NULL;
    if (has_tip(w)) return w;
    if (!GTK_IS_CONTAINER(w)) return NULL;
    GList *kids = gtk_container_get_children(GTK_CONTAINER(w)), *l;
    GtkWidget *hit = NULL;
    for (l = kids; l && !hit; l = l->next) hit = find_tip(l->data, FALSE);
    g_list_free(kids);
    return hit;
}

static gboolean named(GtkWidget *w, const char *name, gboolean top) {
    if (!top && GTK_IS_EVENT_BOX(w)) return FALSE;
    if (!g_strcmp0(gtk_widget_get_name(w), name)) return TRUE;
    if (!GTK_IS_CONTAINER(w)) return FALSE;
    GList *kids = gtk_container_get_children(GTK_CONTAINER(w)), *l;
    gboolean hit = FALSE;
    for (l = kids; l && !hit; l = l->next) hit = named(l->data, name, FALSE);
    g_list_free(kids);
    return hit;
}

static GtkWidget *find_named(GtkWidget *w, const char *name) {
    if (!g_strcmp0(gtk_widget_get_name(w), name)) return w;
    if (!GTK_IS_CONTAINER(w)) return NULL;
    GList *kids = gtk_container_get_children(GTK_CONTAINER(w)), *l;
    GtkWidget *hit = NULL;
    for (l = kids; l && !hit; l = l->next) hit = find_named(l->data, name);
    g_list_free(kids);
    return hit;
}

static GtkWidget *find_label(GtkWidget *w) {
    if (GTK_IS_LABEL(w)) return w;
    if (!GTK_IS_CONTAINER(w)) return NULL;
    GList *kids = gtk_container_get_children(GTK_CONTAINER(w)), *l;
    GtkWidget *hit = NULL;
    for (l = kids; l && !hit; l = l->next) hit = find_label(l->data);
    g_list_free(kids);
    return hit;
}

static gboolean on_bar(GtkWidget *w) {
    GtkWidget *top = gtk_widget_get_toplevel(w);
    return top != popup && GTK_IS_WINDOW(top) && gtk_layer_is_layer_window(GTK_WINDOW(top));
}

/* ---- the card's content ------------------------------------------------------------------------- */

static void add_class(GtkWidget *w, const char *cls) {
    gtk_style_context_add_class(gtk_widget_get_style_context(w), cls);
}

static GtkWidget *hbox(int spacing) {
    return gtk_box_new(GTK_ORIENTATION_HORIZONTAL, spacing);
}

static GtkWidget *vbox(int spacing) {
    return gtk_box_new(GTK_ORIENTATION_VERTICAL, spacing);
}

static GtkWidget *text(const char *markup, const char *cls) {
    GtkWidget *l = gtk_label_new(NULL);
    if (pango_parse_markup(markup, -1, 0, NULL, NULL, NULL, NULL))
        gtk_label_set_markup(GTK_LABEL(l), markup);
    else
        gtk_label_set_text(GTK_LABEL(l), markup);
    gtk_label_set_xalign(GTK_LABEL(l), 0);
    gtk_label_set_ellipsize(GTK_LABEL(l), PANGO_ELLIPSIZE_END);   /* a long window or song title */
    int widest = 0;   /* its longest line, so the card sizes to the text, not to a label's minimum */
    gchar **lines = g_strsplit(gtk_label_get_text(GTK_LABEL(l)), "\n", -1);
    for (gchar **ln = lines; *ln; ln++) widest = MAX(widest, (int)g_utf8_strlen(*ln, -1));
    g_strfreev(lines);
    gtk_label_set_width_chars(GTK_LABEL(l), MIN(widest, 60));
    gtk_label_set_max_width_chars(GTK_LABEL(l), 60);
    add_class(l, cls);
    return l;
}

/* markup's plain text, runs of spaces made one */
static gchar *plain(const char *markup) {
    gchar *t = NULL;
    if (!pango_parse_markup(markup, -1, 0, NULL, &t, NULL, NULL)) t = g_strdup(markup);
    gchar *one = g_regex_replace_literal(space_re, g_strstrip(t), -1, 0, " ", 0, NULL);
    g_free(t);
    return one;
}

/* "Wi-Fi · HomeNet" → "Wi-Fi" and "HomeNet" (NULL when there's no " · ") */
static void split_title(const char *markup, gchar **main, gchar **detail) {
    gchar *t = plain(markup), *dot = strstr(t, " · ");
    *detail = dot ? g_strdup(dot + strlen(" · ")) : NULL;
    if (dot) *dot = 0;
    *main = t;
}

/* the module's icon: the first character of its label, when it's an icon-font glyph */
static gchar *module_icon(GtkWidget *eb) {
    GtkWidget *lab = eb ? find_label(eb) : NULL;
    const char *s = lab ? gtk_label_get_text(GTK_LABEL(lab)) : NULL;
    if (!s || !*s) return NULL;
    gunichar c = g_utf8_get_char(s);
    if (!((c >= 0xE000 && c <= 0xF8FF) || c >= 0xF0000)) return NULL;
    return g_strndup(s, g_utf8_next_char(s) - s);
}

/* An icon centred by the shape it draws, not by its character cell: icon-font glyphs are drawn wider
   than their cell, so a label would set each one off by a different amount. Its background, border,
   colour and size come from its CSS class (a tile lights up under the pointer). */
static gboolean draw_glyph(GtkWidget *w, cairo_t *cr, gpointer d) {
    GtkStyleContext *sc = gtk_widget_get_style_context(w);
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    gtk_render_background(sc, cr, 0, 0, W, H);
    gtk_render_frame(sc, cr, 0, 0, W, H);
    PangoLayout *l = gtk_widget_create_pango_layout(w, g_object_get_data(G_OBJECT(w), "glyph"));
    PangoRectangle ink;
    pango_layout_get_pixel_extents(l, &ink, NULL);
    GdkRGBA c;
    gtk_style_context_get_color(sc, gtk_style_context_get_state(sc), &c);
    gdk_cairo_set_source_rgba(cr, &c);
    cairo_move_to(cr, (W - ink.width) / 2 - ink.x, (H - ink.height) / 2 - ink.y);
    pango_cairo_show_layout(cr, l);
    g_object_unref(l);
    return TRUE;
}

#define TILE 30   /* px: a hub row's icon tile (a drawing area has no CSS min size in GTK 3) */

static GtkWidget *glyph(const char *icon, const char *cls, int square) {
    GtkWidget *w = gtk_drawing_area_new();
    g_object_set_data_full(G_OBJECT(w), "glyph", g_strdup(icon ? icon : ""), g_free);
    add_class(w, cls);
    g_signal_connect(w, "draw", G_CALLBACK(draw_glyph), NULL);
    PangoLayout *l = gtk_widget_create_pango_layout(w, icon ? icon : "");
    PangoRectangle ink, log;
    pango_layout_get_pixel_extents(l, &ink, &log);
    if (square) gtk_widget_set_size_request(w, square, square);
    else gtk_widget_set_size_request(w, ink.width + 2, log.height);
    g_object_unref(l);
    gtk_widget_set_valign(w, GTK_ALIGN_CENTER);
    return w;
}

/* "Super + I" as key caps */
static GtkWidget *key_caps(const char *keys, const char *sep) {
    GtkWidget *box = hbox(3);
    gchar **ks = g_strsplit(keys, sep, -1);
    for (gchar **k = ks; *k; k++) {
        GtkWidget *cap = gtk_label_new(*k);
        add_class(cap, "bartip-key");
        gtk_box_pack_start(GTK_BOX(box), cap, FALSE, FALSE, 0);
    }
    g_strfreev(ks);
    gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
    return box;
}

/* "click · Super + Space": words stay dim text, a shortcut becomes key caps */
static GtkWidget *hint_widget(const char *hint) {
    GtkWidget *box = hbox(8);
    gchar **parts = g_strsplit(hint, " · ", -1);
    for (gchar **p = parts; *p; p++)
        gtk_box_pack_start(GTK_BOX(box), g_regex_match(keys_re, *p, 0, NULL) ? key_caps(*p, " + ")
                                                                            : text(*p, "bartip-hint"),
                           FALSE, FALSE, 0);
    g_strfreev(parts);
    gtk_widget_set_valign(box, GTK_ALIGN_CENTER);
    return box;
}

static void add_rule(GtkWidget *box) {
    GtkWidget *rule = gtk_separator_new(GTK_ORIENTATION_HORIZONTAL);
    add_class(rule, "bartip-rule");
    gtk_box_pack_start(GTK_BOX(box), rule, FALSE, FALSE, 0);
}

/* The hints under the rule: "Click: networks · Right-click / Scroll: …" → the action as a cap, what it
   does dimmed beside it; one line of them per line given. */
static void add_footer(GtkWidget *box, const char *hints) {
    add_rule(box);
    gchar **lines = g_strsplit(hints, "\n", -1);
    for (gchar **ln = lines; *ln; ln++) {
        GtkWidget *line = hbox(12);
        gchar **parts = g_strsplit(*ln, " · ", -1);
        for (gchar **p = parts; *p; p++) {
            gchar *colon = strstr(*p, ": ");
            if (!colon) {
                gtk_box_pack_start(GTK_BOX(line), text(*p, "bartip-hint"), FALSE, FALSE, 0);
                continue;
            }
            gchar *verb = g_strndup(*p, colon - *p);
            verb[0] = g_ascii_toupper(verb[0]);
            GtkWidget *pair = hbox(5);
            gtk_box_pack_start(GTK_BOX(pair), key_caps(verb, " / "), FALSE, FALSE, 0);
            gtk_box_pack_start(GTK_BOX(pair), text(colon + 2, "bartip-hint"), FALSE, FALSE, 0);
            gtk_box_pack_start(GTK_BOX(line), pair, FALSE, FALSE, 0);
            g_free(verb);
        }
        g_strfreev(parts);
        gtk_box_pack_start(GTK_BOX(box), line, FALSE, FALSE, 0);
    }
    g_strfreev(lines);
}

/* the header: the module's icon, the name in bold, what it's about dimmed after it */
static GtkWidget *title_widget(const char *markup, const char *icon) {
    gchar *main, *detail;
    split_title(markup, &main, &detail);
    GtkWidget *box = hbox(7);
    if (icon) gtk_box_pack_start(GTK_BOX(box), glyph(icon, "bartip-icon", 0), FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), text(main, "bartip-title"), FALSE, FALSE, 0);
    if (detail) gtk_box_pack_start(GTK_BOX(box), text(detail, "bartip-detail"), FALSE, FALSE, 0);
    g_free(main); g_free(detail);
    return box;
}

/* A tooltip: a header (icon, title, keys), then its lines — "value  <dim>what</dim>" as a two-column
   list, what on the left, the value on the right — then the hints. */
static void build_tooltip(const char *markup, const char *icon) {
    gchar **lines = g_strsplit(markup, "\n", -1);
    GString *foot = g_string_new(NULL);
    GtkWidget *grid = NULL;
    int grid_rows = 0;
    gboolean first = TRUE;
    for (gchar **ln = lines; *ln; ln++) {
        const char *line = g_strstrip(*ln);
        GMatchInfo *mi = NULL;
        if (!*line) continue;
        if (g_regex_match(row_re, line, 0, &mi)) {            /* title + keys / hint */
            gchar *title = g_match_info_fetch(mi, 1), *hint = g_match_info_fetch(mi, 2);
            GtkWidget *row = hbox(28);
            gtk_box_pack_start(GTK_BOX(row), title_widget(title, first ? icon : NULL), FALSE, FALSE, 0);
            gtk_box_pack_end(GTK_BOX(row), hint_widget(hint), FALSE, FALSE, 0);
            add_class(row, "bartip-head");
            gtk_box_pack_start(GTK_BOX(card), row, FALSE, FALSE, 0);
            g_free(title); g_free(hint);
            grid = NULL;
        } else if (first) {
            GtkWidget *head = title_widget(line, icon);
            add_class(head, "bartip-head");
            gtk_box_pack_start(GTK_BOX(card), head, FALSE, FALSE, 0);
        } else if (g_regex_match(hint_re, line, 0, NULL)) {
            gchar *p = plain(line);
            g_string_append_printf(foot, "%s%s", foot->len ? "\n" : "", p);
            g_free(p);
        } else if (g_regex_match(kv_re, line, 0, &mi)) {     /* "60 fps  <dim>while something moved</dim>" */
            gchar *value = g_match_info_fetch(mi, 1), *what = g_match_info_fetch(mi, 2);
            gchar *pv = plain(value), *pw = plain(what);
            if (!grid) {
                grid = gtk_grid_new();
                gtk_grid_set_column_spacing(GTK_GRID(grid), 24);
                gtk_grid_set_row_spacing(GTK_GRID(grid), 2);
                add_class(grid, "bartip-kv");
                gtk_box_pack_start(GTK_BOX(card), grid, FALSE, FALSE, 0);
                grid_rows = 0;
            }
            GtkWidget *w = text(pw, "bartip-kv-what"), *v = text(pv, "bartip-kv-value");
            gtk_widget_set_hexpand(w, TRUE);
            gtk_label_set_xalign(GTK_LABEL(v), 1);
            gtk_grid_attach(GTK_GRID(grid), w, 0, grid_rows, 1, 1);
            gtk_grid_attach(GTK_GRID(grid), v, 1, grid_rows++, 1, 1);
            g_free(value); g_free(what); g_free(pv); g_free(pw);
        } else {
            gtk_box_pack_start(GTK_BOX(card), text(line, "bartip-body"), FALSE, FALSE, 0);
            grid = NULL;
        }
        g_match_info_free(mi);
        first = FALSE;
    }
    if (foot->len) add_footer(card, foot->str);
    g_string_free(foot, TRUE);
    g_strfreev(lines);
}

#include "cards.c"   /* the rich cards: a tooltip that is JSON (storage, memory, GPU) */

static GtkWidget *cell(const char *s, const char *cls, const char *cls2, const char *cls3) {
    GtkWidget *l = gtk_label_new(s);
    add_class(l, cls);
    if (cls2) add_class(l, cls2);
    if (cls3) add_class(l, cls3);
    return l;
}

static void build_calendar(void) {
    GDateTime *now = g_date_time_new_now_local();
    GDate today;
    g_date_set_dmy(&today, g_date_time_get_day_of_month(now), g_date_time_get_month(now),
                   g_date_time_get_year(now));
    int months = g_date_time_get_year(now) * 12 + g_date_time_get_month(now) - 1 + month_offset;
    int year = months / 12, month = months % 12 + 1;
    g_date_time_unref(now);

    static const char *names[] = {"January", "February", "March", "April", "May", "June", "July",
                                  "August", "September", "October", "November", "December"};
    gchar *name = g_strdup(names[month - 1]), *yr = g_strdup_printf("%d", year);   /* English, as the clock */
    GtkWidget *head = hbox(8);
    gtk_box_pack_start(GTK_BOX(head), text(name, "bartip-title"), FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(head), text(yr, "bartip-hint"), FALSE, FALSE, 0);
    if (month_offset) {   /* how far from today's month, when scrolled away */
        gchar *away = g_strdup_printf("%+d", month_offset);
        gtk_box_pack_end(GTK_BOX(head), cell(away, "bartip-away", NULL, NULL), FALSE, FALSE, 0);
        g_free(away);
    }
    gtk_box_pack_start(GTK_BOX(card), head, FALSE, FALSE, 0);
    g_free(name); g_free(yr);

    GtkWidget *grid = gtk_grid_new();
    add_class(grid, "bartip-cal");
    static const char *dows[] = {"Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"};
    gtk_grid_attach(GTK_GRID(grid), cell("wk", "cal-wk", "cal-dow", NULL), 0, 0, 1, 1);
    for (int c = 0; c < 7; c++)
        gtk_grid_attach(GTK_GRID(grid), cell(dows[c], "cal-dow", c >= 5 ? "weekend" : NULL, NULL), c + 1, 0, 1, 1);

    GDate d;   /* the Monday on or before the 1st; always six weeks, so scrolling never resizes it */
    g_date_set_dmy(&d, 1, month, year);
    g_date_subtract_days(&d, g_date_get_weekday(&d) - G_DATE_MONDAY);
    for (int r = 1; r <= 6; r++) {
        GDate thu = d;   /* Thursday decides the ISO week */
        g_date_add_days(&thu, 3);
        gchar *wk = g_strdup_printf("%u", g_date_get_iso8601_week_of_year(&thu));
        gtk_grid_attach(GTK_GRID(grid), cell(wk, "cal-wk", NULL, NULL), 0, r, 1, 1);
        g_free(wk);
        for (int c = 0; c < 7; c++, g_date_add_days(&d, 1)) {
            gchar *day = g_strdup_printf("%u", g_date_get_day(&d));
            gtk_grid_attach(GTK_GRID(grid),
                            cell(day, "cal-day",
                                 !g_date_compare(&d, &today) ? "today"
                                 : g_date_get_month(&d) != (GDateMonth)month ? "out" : NULL,
                                 c >= 5 ? "weekend" : NULL),
                            c + 1, r, 1, 1);
            g_free(day);
        }
    }
    gtk_box_pack_start(GTK_BOX(card), grid, FALSE, FALSE, 0);
    add_footer(card, "Scroll: month · Click: long date");
}

/* ---- the hub ------------------------------------------------------------------------------------ */

static void hide_card(void);
static void rebuild(void);

/* An event as if it happened on the module itself: waybar's own handlers run its on-click etc. */
static void forward(GtkWidget *eb, GdkEventType type, guint button, GdkScrollDirection dir) {
    if (!gtk_widget_get_realized(eb)) gtk_widget_realize(eb);
    GdkEvent *e = gdk_event_new(type);
    GdkDevice *ptr = gdk_seat_get_pointer(gdk_display_get_default_seat(gtk_widget_get_display(eb)));
    gdk_event_set_device(e, ptr);
    if (type == GDK_SCROLL) {
        e->scroll.window = g_object_ref(gtk_widget_get_window(eb));
        e->scroll.direction = dir;
        e->scroll.time = GDK_CURRENT_TIME;
    } else {
        e->button.window = g_object_ref(gtk_widget_get_window(eb));
        e->button.button = button;
        e->button.time = GDK_CURRENT_TIME;
    }
    gtk_widget_event(eb, e);
    gdk_event_free(e);
}

static gboolean row_click(GtkWidget *row, GdkEventButton *e, gpointer d) {
    GtkWidget *eb = g_object_get_data(G_OBJECT(row), "module");
    if (!eb) return FALSE;
    forward(eb, GDK_BUTTON_PRESS, e->button, 0);
    forward(eb, GDK_BUTTON_RELEASE, e->button, 0);
    if (e->button == 1) hide_card();   /* it opened something; a right-click (mute) keeps the menu */
    return TRUE;
}

/* GTK 3 doesn't mark an event box hovered by itself (waybar's modules set it the same way): this
   gives the row, and its tile, their :hover look */
static gboolean row_cross(GtkWidget *row, GdkEventCrossing *e, gpointer d) {
    if (e->detail == GDK_NOTIFY_INFERIOR) return FALSE;
    if (e->type == GDK_ENTER_NOTIFY) gtk_widget_set_state_flags(row, GTK_STATE_FLAG_PRELIGHT, FALSE);
    else gtk_widget_unset_state_flags(row, GTK_STATE_FLAG_PRELIGHT);
    return FALSE;
}

static gboolean row_scroll(GtkWidget *row, GdkEventScroll *e, gpointer d) {
    GtkWidget *eb = g_object_get_data(G_OBJECT(row), "module");
    int dir = -1;
    if (e->direction == GDK_SCROLL_UP || e->direction == GDK_SCROLL_DOWN) dir = e->direction;
    else if (e->direction == GDK_SCROLL_SMOOTH) {
        scroll_sum += e->delta_y;
        if (scroll_sum <= -1 || scroll_sum >= 1) { dir = scroll_sum > 0 ? GDK_SCROLL_DOWN : GDK_SCROLL_UP; scroll_sum = 0; }
    }
    if (eb && dir >= 0) forward(eb, GDK_SCROLL, 0, dir);
    return TRUE;
}

/* A row: the module's icon in a tile, its tooltip's title and first line under it; on the right its
   keys (a panel) or its value (a state: "44%", with a meter under the name). Sets *status for a state. */
static GtkWidget *hub_row(GtkWidget *eb, gboolean *status) {
    GtkWidget *tipw = find_tip(eb, TRUE);
    gchar *m = tipw ? gtk_widget_get_tooltip_markup(tipw) : NULL;
    gchar *icon = module_icon(eb);
    gchar *title = NULL, *keys = NULL, *sub = NULL, *sub_what = NULL;
    gchar **lines = g_strsplit(m ? m : "", "\n", -1);
    for (gchar **ln = lines; *ln; ln++) {
        const char *line = g_strstrip(*ln);
        GMatchInfo *mi = NULL;
        if (!*line) continue;
        if (!title) {
            if (g_regex_match(row_re, line, 0, &mi)) {
                title = g_match_info_fetch(mi, 1);
                keys = g_match_info_fetch(mi, 2);
            } else {
                title = g_strdup(line);
            }
        } else if (!sub && !g_regex_match(hint_re, line, 0, NULL)) {
            if (g_regex_match(kv_re, line, 0, &mi)) {   /* "1  <dim>devices connected</dim>" */
                gchar *v = g_match_info_fetch(mi, 1), *w = g_match_info_fetch(mi, 2);
                sub = plain(v); sub_what = plain(w);
                g_free(v); g_free(w);
            } else {
                sub = plain(line);
            }
        }
        g_match_info_free(mi);
    }
    g_strfreev(lines);
    *status = !keys;

    gchar *main = NULL, *detail = NULL;
    split_title(title ? title : gtk_widget_get_name(eb), &main, &detail);
    GMatchInfo *pm = NULL;
    int pct = -1;
    if (detail && g_regex_match(pct_re, detail, 0, &pm)) {
        gchar *n = g_match_info_fetch(pm, 1);
        pct = atoi(n);
        g_free(n);
    }
    g_match_info_free(pm);

    GtkWidget *row = gtk_event_box_new();
    add_class(row, "bartip-row");
    gtk_widget_add_events(row, GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK | GDK_SCROLL_MASK |
                               GDK_SMOOTH_SCROLL_MASK | GDK_ENTER_NOTIFY_MASK | GDK_LEAVE_NOTIFY_MASK);
    g_object_set_data(G_OBJECT(row), "module", eb);
    g_signal_connect(row, "button-release-event", G_CALLBACK(row_click), NULL);
    g_signal_connect(row, "scroll-event", G_CALLBACK(row_scroll), NULL);
    g_signal_connect(row, "enter-notify-event", G_CALLBACK(row_cross), NULL);
    g_signal_connect(row, "leave-notify-event", G_CALLBACK(row_cross), NULL);

    GtkWidget *line = hbox(11), *words = vbox(2);
    GtkWidget *tile = glyph(icon, "bartip-tile", TILE);
    gtk_box_pack_start(GTK_BOX(line), tile, FALSE, FALSE, 0);

    GtkWidget *name = hbox(7);
    gtk_box_pack_start(GTK_BOX(name), text(main, "bartip-title"), FALSE, FALSE, 0);
    if (detail && pct < 0) gtk_box_pack_start(GTK_BOX(name), text(detail, "bartip-detail"), FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(words), name, FALSE, FALSE, 0);
    if (sub && *sub) {   /* one line, kept short so the menu stays narrow */
        GtkWidget *s = hbox(5), *l = text(sub, sub_what ? "bartip-value" : "bartip-sub");
        gtk_label_set_max_width_chars(GTK_LABEL(l), 40);
        gtk_label_set_width_chars(GTK_LABEL(l), MIN(gtk_label_get_width_chars(GTK_LABEL(l)), 40));
        gtk_box_pack_start(GTK_BOX(s), l, FALSE, FALSE, 0);
        if (sub_what) gtk_box_pack_start(GTK_BOX(s), text(sub_what, "bartip-sub"), FALSE, FALSE, 0);
        gtk_box_pack_start(GTK_BOX(words), s, FALSE, FALSE, 0);
    }
    if (pct >= 0) {   /* the volume: how loud, as a meter */
        GtkWidget *meter = gtk_progress_bar_new();
        gtk_progress_bar_set_fraction(GTK_PROGRESS_BAR(meter), CLAMP(pct, 0, 100) / 100.0);
        add_class(meter, "bartip-meter");
        gtk_box_pack_start(GTK_BOX(words), meter, FALSE, FALSE, 0);
    }
    gtk_widget_set_valign(words, GTK_ALIGN_CENTER);
    gtk_box_pack_start(GTK_BOX(line), words, TRUE, TRUE, 0);
    if (keys) {
        gtk_box_pack_end(GTK_BOX(line), hint_widget(keys), FALSE, FALSE, 0);
    } else if (pct >= 0) {
        GtkWidget *v = text(detail, "bartip-big");
        gtk_widget_set_valign(v, GTK_ALIGN_CENTER);
        gtk_box_pack_end(GTK_BOX(line), v, FALSE, FALSE, 0);
    }
    gtk_container_add(GTK_CONTAINER(row), line);
    g_free(m); g_free(icon); g_free(title); g_free(keys); g_free(sub); g_free(sub_what);
    g_free(main); g_free(detail);
    return row;
}

static void add_heading(const char *name) {
    GtkWidget *h = text(name, "bartip-heading");
    gtk_box_pack_start(GTK_BOX(card), h, FALSE, FALSE, 0);
}

/* the panels first (they have keys), then the states (Bluetooth, network, volume) */
static void build_hub(void) {
    GPtrArray *open = g_ptr_array_new(), *state = g_ptr_array_new();
    for (guint i = 0; hub_items && i < hub_items->len; i++) {
        GtkWidget *eb = g_ptr_array_index(hub_items, i);
        gboolean status;
        if (!gtk_widget_get_visible(eb)) continue;   /* a module that hides itself stays out (no Bluetooth) */
        GtkWidget *row = hub_row(eb, &status);
        g_ptr_array_add(status ? state : open, row);
    }
    add_class(card, "bartip-hub");
    if (open->len) add_heading("OPEN");
    for (guint i = 0; i < open->len; i++) gtk_box_pack_start(GTK_BOX(card), open->pdata[i], FALSE, FALSE, 0);
    if (state->len) add_heading("STATUS");
    for (guint i = 0; i < state->len; i++) gtk_box_pack_start(GTK_BOX(card), state->pdata[i], FALSE, FALSE, 0);
    g_ptr_array_free(open, TRUE);
    g_ptr_array_free(state, TRUE);
    add_footer(card, "Click: open · Right-click / Scroll: as on its icon");
}

static void hub_changed(GObject *o, GParamSpec *p, gpointer d) {
    if (popup && gtk_widget_get_visible(popup) && kind == HUB) rebuild();   /* the volume while scrolling */
}

static void watch(GtkWidget *w, gpointer d) {
    g_signal_connect(w, "notify::tooltip-markup", G_CALLBACK(hub_changed), NULL);
    if (GTK_IS_LABEL(w)) g_signal_connect(w, "notify::label", G_CALLBACK(hub_changed), NULL);
    if (GTK_IS_CONTAINER(w)) gtk_container_forall(GTK_CONTAINER(w), watch, NULL);
}

static void item_gone(gpointer d, GObject *was) {
    if (hub_items) g_ptr_array_remove(hub_items, was);
}

/* Move the hub group's other modules off the bar, once the bar is built (waybar makes the modules,
   then puts them in the window). */
static gboolean adopt(gpointer root) {
    static int tries;
    GtkWidget *top = gtk_widget_get_toplevel(root);
    GtkWidget *hub = gtk_widget_is_toplevel(top) ? find_named(top, "custom-hub") : NULL;
    if (!hub) return ++tries < 50 ? G_SOURCE_CONTINUE : G_SOURCE_REMOVE;   /* 5 s; no hub: nothing to do */
    while (hub && !GTK_IS_EVENT_BOX(hub)) hub = gtk_widget_get_parent(hub);
    GtkWidget *group = hub ? gtk_widget_get_parent(hub) : NULL;
    if (!group) return G_SOURCE_REMOVE;
    if (!holder) {
        holder = gtk_offscreen_window_new();
        gtk_container_add(GTK_CONTAINER(holder), gtk_box_new(GTK_ORIENTATION_VERTICAL, 0));
        gtk_widget_show(gtk_bin_get_child(GTK_BIN(holder)));
        gtk_widget_show(holder);
        hub_items = g_ptr_array_new();
    }
    GtkWidget *keep = gtk_bin_get_child(GTK_BIN(holder));
    GList *kids = gtk_container_get_children(GTK_CONTAINER(group)), *l;
    for (l = kids; l; l = l->next) {
        GtkWidget *k = l->data;
        if (k == hub) continue;
        g_object_ref(k);
        gtk_container_remove(GTK_CONTAINER(group), k);
        gtk_box_pack_start(GTK_BOX(keep), k, FALSE, FALSE, 0);
        g_object_unref(k);
        g_ptr_array_add(hub_items, k);
        g_object_weak_ref(G_OBJECT(k), item_gone, NULL);
        watch(k, NULL);
    }
    g_list_free(kids);
    return G_SOURCE_REMOVE;
}

/* ---- the card ----------------------------------------------------------------------------------- */

static void make_popup(void) {
    popup = gtk_window_new(GTK_WINDOW_TOPLEVEL);
    GdkScreen *screen = gtk_widget_get_screen(popup);
    GdkVisual *rgba = gdk_screen_get_rgba_visual(screen);
    if (rgba) gtk_widget_set_visual(popup, rgba);
    gtk_widget_set_app_paintable(popup, TRUE);
    add_class(popup, "bartip");
    gtk_layer_init_for_window(GTK_WINDOW(popup));
    gtk_layer_set_namespace(GTK_WINDOW(popup), "bartip");
    gtk_layer_set_layer(GTK_WINDOW(popup), GTK_LAYER_SHELL_LAYER_OVERLAY);
    gtk_layer_set_anchor(GTK_WINDOW(popup), GTK_LAYER_SHELL_EDGE_TOP, TRUE);
    gtk_layer_set_anchor(GTK_WINDOW(popup), GTK_LAYER_SHELL_EDGE_LEFT, TRUE);
    gtk_layer_set_exclusive_zone(GTK_WINDOW(popup), 0);   /* 0: kept below the bar's own zone */
    gtk_layer_set_keyboard_mode(GTK_WINDOW(popup), GTK_LAYER_SHELL_KEYBOARD_MODE_NONE);
    /* the pointer can rest on it (it stays while hovered) and scroll the calendar there too */
    gtk_widget_add_events(popup, GDK_ENTER_NOTIFY_MASK | GDK_LEAVE_NOTIFY_MASK | GDK_SCROLL_MASK |
                                 GDK_SMOOTH_SCROLL_MASK);
    card = gtk_box_new(GTK_ORIENTATION_VERTICAL, 4);
    add_class(card, "bartip-card");
    gtk_container_add(GTK_CONTAINER(popup), card);
}

static void hide_card(void) {
    if (show_timer) { g_source_remove(show_timer); show_timer = 0; }
    if (hide_timer) { g_source_remove(hide_timer); hide_timer = 0; }
    if (popup && gtk_widget_get_visible(popup)) {
        gtk_widget_hide(popup);
        hidden_at = g_get_monotonic_time();
    }
}

static void place(void) {
    GtkWidget *bar = gtk_widget_get_toplevel(anchor);
    GdkMonitor *mon = gtk_layer_get_monitor(GTK_WINDOW(bar));
    if (!mon) mon = gdk_display_get_monitor_at_window(gtk_widget_get_display(bar), gtk_widget_get_window(bar));
    if (gtk_widget_get_visible(popup) && gtk_layer_get_monitor(GTK_WINDOW(popup)) != mon)
        gtk_widget_hide(popup);   /* another screen's bar: a layer surface moves screens by remapping */
    gtk_layer_set_monitor(GTK_WINDOW(popup), mon);

    GdkRectangle geo;
    gdk_monitor_get_geometry(mon, &geo);
    int x = 0, y = 0;
    gtk_widget_translate_coordinates(anchor, bar, 0, 0, &x, &y);
    GtkRequisition nat;
    gtk_widget_get_preferred_size(popup, NULL, &nat);
    int left = x + gtk_widget_get_allocated_width(anchor) / 2 - nat.width / 2;
    left = CLAMP(left, 4, MAX(4, geo.width - nat.width - 4));
    gtk_layer_set_margin(GTK_WINDOW(popup), GTK_LAYER_SHELL_EDGE_LEFT, left);
    gtk_layer_set_margin(GTK_WINDOW(popup), GTK_LAYER_SHELL_EDGE_TOP, 4);
    gtk_window_resize(GTK_WINDOW(popup), 1, 1);   /* shrink to the new content */
}

static void rebuild(void) {
    if (!anchor) { hide_card(); return; }   /* its module went (a tray icon closed) */
    gtk_container_foreach(GTK_CONTAINER(card), (GtkCallback)gtk_widget_destroy, NULL);
    gtk_style_context_remove_class(gtk_widget_get_style_context(card), "bartip-hub");
    gtk_style_context_remove_class(gtk_widget_get_style_context(card), "bartip-rich");
    if (kind == CLOCK) {
        build_calendar();
    } else if (kind == HUB) {
        build_hub();
    } else {
        gchar *m = source ? gtk_widget_get_tooltip_markup(source) : NULL;
        if (!m || !*m) { g_free(m); hide_card(); return; }
        if (!build_rich(m)) {
            gchar *icon = module_icon(anchor);
            build_tooltip(m, icon);
            g_free(icon);
        }
        g_free(m);
    }
    gtk_widget_show_all(card);
    /* A card updating while it's shown (a speed, a temperature, a list of apps) keeps the largest size it
       had for this module, so a shorter number never shrinks it, moves it or reflows it under the
       pointer; it opens at its own size again next time. */
    gboolean live = gtk_widget_get_visible(popup) && kept_for == anchor;
    gtk_widget_set_size_request(card, -1, -1);
    GtkRequisition nat;
    gtk_widget_get_preferred_size(card, NULL, &nat);
    keep_w = live ? MAX(keep_w, nat.width) : nat.width;
    keep_h = live ? MAX(keep_h, nat.height) : nat.height;
    g_set_weak_pointer(&kept_for, anchor);
    gtk_widget_set_size_request(card, keep_w, keep_h);
    place();
}

static void show_card(void) {
    if (!anchor) return;
    if (!popup) make_popup();
    rebuild();
    if (anchor && (kind != TIP || source) && !gtk_widget_get_visible(popup)) {
        gtk_widget_show_all(popup);
    }
}

static gboolean show_due(gpointer d) {
    show_timer = 0;
    show_card();
    return G_SOURCE_REMOVE;
}

static gboolean hide_due(gpointer d) {
    hide_timer = 0;
    hide_card();
    g_clear_weak_pointer(&anchor);
    return G_SOURCE_REMOVE;
}

static void source_changed(GObject *o, GParamSpec *p, gpointer d) {
    if (popup && gtk_widget_get_visible(popup) && kind == TIP) rebuild();   /* live: the volume while scrolling */
}

static void set_source(GtkWidget *w) {
    if (source && source_notify) g_signal_handler_disconnect(source, source_notify);
    source_notify = 0;
    g_set_weak_pointer(&source, w);
    if (w) source_notify = g_signal_connect(w, "notify::tooltip-markup", G_CALLBACK(source_changed), NULL);
}

/* ---- the hooks: every widget's events, in every waybar module ---------------------------------- */

static gboolean on_enter(GSignalInvocationHint *h, guint n, const GValue *v, gpointer d) {
    GtkWidget *w = g_value_get_object(&v[0]);
    if (popup && w == popup) {   /* onto the card: keep it */
        if (hide_timer) { g_source_remove(hide_timer); hide_timer = 0; }
        return TRUE;
    }
    if (!on_bar(w)) return TRUE;
    stop_tooltips(gtk_widget_get_toplevel(w));   /* cheap, and catches tray icons added since */
    int is = named(w, "custom-clock", TRUE) ? CLOCK : named(w, "custom-hub", TRUE) ? HUB : TIP;
    GtkWidget *tip = is == TIP ? find_tip(w, TRUE) : NULL;
    if (!tip && is == TIP) {   /* the bar's background, a module without a tooltip */
        if (!hide_timer) hide_timer = g_timeout_add(HIDE_DELAY, hide_due, NULL);
        return TRUE;
    }
    if (hide_timer) { g_source_remove(hide_timer); hide_timer = 0; }
    if (w == anchor && (clicked || (popup && gtk_widget_get_visible(popup)))) return TRUE;
    clicked = FALSE;
    g_set_weak_pointer(&anchor, w);
    set_source(tip);
    kind = is;
    month_offset = 0;
    scroll_sum = 0;
    if (is == CLOCK) gtk_widget_add_events(w, GDK_SCROLL_MASK | GDK_SMOOTH_SCROLL_MASK);
    if (show_timer) { g_source_remove(show_timer); show_timer = 0; }
    if ((popup && gtk_widget_get_visible(popup)) || g_get_monotonic_time() - hidden_at < WARM * 1000)
        show_card();
    else
        show_timer = g_timeout_add(SHOW_DELAY, show_due, NULL);
    return TRUE;
}

static gboolean on_leave(GSignalInvocationHint *h, guint n, const GValue *v, gpointer d) {
    GtkWidget *w = g_value_get_object(&v[0]);
    GdkEventCrossing *e = g_value_get_boxed(&v[1]);
    if (e->detail == GDK_NOTIFY_INFERIOR) return TRUE;   /* into a child: still over it */
    if (popup && w == popup) {   /* off the card: back to its module keeps it, anywhere else hides it */
        if (!hide_timer) hide_timer = g_timeout_add(HIDE_DELAY, hide_due, NULL);
        return TRUE;
    }
    if (!on_bar(w)) return TRUE;
    if (w == anchor || w == gtk_widget_get_toplevel(w)) {
        if (show_timer) { g_source_remove(show_timer); show_timer = 0; }
        if (!hide_timer) hide_timer = g_timeout_add(HIDE_DELAY, hide_due, NULL);
        clicked = FALSE;
    }
    return TRUE;
}

/* a click hides it, like GTK's, until the pointer moves to another module; on the hub it opens or
   closes the menu */
static gboolean on_press(GSignalInvocationHint *h, guint n, const GValue *v, gpointer d) {
    GtkWidget *w = g_value_get_object(&v[0]);
    if (!on_bar(w) || !anchor) return TRUE;
    if (w == anchor && kind == HUB && !(popup && gtk_widget_get_visible(popup))) {
        clicked = FALSE;
        show_card();
        return TRUE;
    }
    clicked = TRUE;
    hide_card();
    return TRUE;
}

static gboolean on_scroll(GSignalInvocationHint *h, guint n, const GValue *v, gpointer d) {
    GtkWidget *w = g_value_get_object(&v[0]);
    GdkEventScroll *e = g_value_get_boxed(&v[1]);
    if ((w != anchor && w != popup) || kind != CLOCK) return TRUE;   /* over the clock or its card */
    int step = 0;
    if (e->direction == GDK_SCROLL_UP) step = -1;
    else if (e->direction == GDK_SCROLL_DOWN) step = 1;
    else if (e->direction == GDK_SCROLL_SMOOTH) {   /* a touchpad: a month per full notch */
        scroll_sum += e->delta_y;
        if (scroll_sum <= -1 || scroll_sum >= 1) { step = scroll_sum > 0 ? 1 : -1; scroll_sum = 0; }
    }
    if (step) {
        month_offset += step;
        clicked = FALSE;
        show_card();
    }
    return TRUE;
}

static void hook(const char *signal, GSignalEmissionHook fn) {
    g_signal_add_emission_hook(g_signal_lookup(signal, GTK_TYPE_WIDGET), 0, fn, NULL, NULL);
}

typedef struct {   /* waybar's wbcffi_init_info (ABI 2) */
    void *obj;
    const char *waybar_version;
    GtkContainer *(*get_root_widget)(void *obj);
    void (*queue_update)(void *obj);
} wbcffi_init_info;

void *wbcffi_init(const wbcffi_init_info *info, const void *config, size_t n) {
    static gboolean hooked;   /* one set of hooks per waybar, however many bars it draws */
    if (!hooked) {
        hooked = TRUE;
        row_re = g_regex_new("^(<b>.*?</b>)\\s{2,}<span alpha='55%'>(.*)</span>$", 0, 0, NULL);
        hint_re = g_regex_new("\\b(click|scroll): ", G_REGEX_CASELESS, 0, NULL);   /* says what a click does */
        kv_re = g_regex_new("^(.+?)\\s{2,}<span alpha='55%'>(.*)</span>$", 0, 0, NULL);
        space_re = g_regex_new("\\s{2,}", 0, 0, NULL);
        pct_re = g_regex_new("^(\\d+)%$", 0, 0, NULL);
        keys_re = g_regex_new("^((Super|Ctrl|Alt|Shift) \\+ )+\\S+$", 0, 0, NULL);
        hook("enter-notify-event", on_enter);
        hook("leave-notify-event", on_leave);
        hook("button-press-event", on_press);
        hook("scroll-event", on_scroll);
    }
    if (info) g_timeout_add(100, adopt, info->get_root_widget(info->obj));
    return GINT_TO_POINTER(1);
}

void wbcffi_deinit(void *instance) {
    hide_card();
}
