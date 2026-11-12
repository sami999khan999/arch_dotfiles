/* cards.c — the rich cards: a module's tooltip that is JSON, drawn as blocks (included by bartips.c).
 *
 * A script (storage.py, memory.py, gpu.py) prints its tooltip as {"bartip": 1, "blocks": [...]}; "<", ">"
 * and "&" escaped as <… so GTK takes it as plain tooltip text. Blocks, top to bottom:
 *
 *   {"type": "head", "icon", "title", "detail", "right", "color"}   the card's header (and "section": a
 *                                                                    part of it, e.g. one drive)
 *   {"type": "kv", "rows": [[what, value, color?], …]}              a two-column list
 *   {"type": "meter", "value": 0-100, "left", "right", "color"?}    a bar; green / amber / red by value
 *   {"type": "stack", "style": "dimm"|"bar", "segments":            parts of a whole: a memory stick
 *        [{"label", "value": %, "color"}, …]}                       drawn around them, a legend under
 *   {"type": "chip", "meters": [{"icon", "value": %, "color"?}]}    a GPU die with a gauge per value
 *   {"type": "bars", "values": [%, …], "labels": [s, …]}            a column per value (a CPU's threads)
 *   {"type": "spark", "values": [%, …]}                             a history, oldest first, as a filled line
 *   {"type": "table", "rows": [[name, …], …]}                       the first column ellipsized
 *   {"type": "text", "text", "color"?}, {"type": "rule"}, {"type": "hints", "text"}
 *
 * Colours are names from waybar's style.css (@define-color: text, subtext, muted, accent, alert, green,
 * amber…): CSS classes for text, looked up by name for cairo, so the colour themes reach the drawings too.
 */
#include <json-glib/json-glib.h>

static const char *jstr(JsonObject *o, const char *k) {
    return o && json_object_has_member(o, k) && JSON_NODE_HOLDS_VALUE(json_object_get_member(o, k))
               ? json_object_get_string_member(o, k) : NULL;
}

static double jnum(JsonObject *o, const char *k) {
    return o && json_object_has_member(o, k) ? json_object_get_double_member(o, k) : 0;
}

static GdkRGBA colour(GtkWidget *w, const char *name) {
    GdkRGBA c = {0.75, 0.79, 0.96, 1};
    if (name) gtk_style_context_lookup_color(gtk_widget_get_style_context(w), name, &c);
    return c;
}

static void set_colour(cairo_t *cr, GtkWidget *w, const char *name, double alpha) {
    GdkRGBA c = colour(w, name);
    cairo_set_source_rgba(cr, c.red, c.green, c.blue, c.alpha * alpha);
}

/* the level's colour when the script didn't give one: fine, getting full, full */
static const char *level_colour(double v) {
    return v < 70 ? "green" : v < 90 ? "amber" : "alert";
}

static GtkWidget *coloured(const char *s, const char *cls, const char *colour_name) {
    GtkWidget *l = text(s ? s : "", cls);
    if (colour_name) {
        gchar *c = g_strdup_printf("c-%s", colour_name);
        add_class(l, c);
        g_free(c);
    }
    return l;
}

/* ---- the drawings --------------------------------------------------------------------------------- */

static gboolean draw_meter(GtkWidget *w, cairo_t *cr, gpointer d) {
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    double v = CLAMP(GPOINTER_TO_INT(g_object_get_data(G_OBJECT(w), "value")) / 1000.0, 0, 100);
    set_colour(cr, w, "overlay", 1);
    cairo_rectangle(cr, 0, 0, W, H);
    cairo_fill(cr);
    set_colour(cr, w, g_object_get_data(G_OBJECT(w), "colour"), 1);
    cairo_rectangle(cr, 0, 0, round(W * v / 100), H);
    cairo_fill(cr);
    return TRUE;
}

static GtkWidget *meter_widget(double value, const char *colour_name, int height) {
    GtkWidget *w = gtk_drawing_area_new();
    g_object_set_data(G_OBJECT(w), "value", GINT_TO_POINTER((int)(value * 1000)));
    g_object_set_data_full(G_OBJECT(w), "colour", g_strdup(colour_name ? colour_name : level_colour(value)), g_free);
    gtk_widget_set_size_request(w, 60, height);
    gtk_widget_set_hexpand(w, TRUE);
    gtk_widget_set_valign(w, GTK_ALIGN_CENTER);
    g_signal_connect(w, "draw", G_CALLBACK(draw_meter), NULL);
    return w;
}

typedef struct { double value; gchar *colour; } Part;

static void free_parts(gpointer p) {
    GArray *a = p;
    for (guint i = 0; i < a->len; i++) g_free(g_array_index(a, Part, i).colour);
    g_array_free(a, TRUE);
}

/* a stacked bar, the parts side by side; empty space is the trough */
static void stacked(cairo_t *cr, GtkWidget *w, GArray *parts, double x, double y, double wd, double ht) {
    set_colour(cr, w, "overlay", 1);
    cairo_rectangle(cr, x, y, wd, ht);
    cairo_fill(cr);
    double at = x;
    for (guint i = 0; i < parts->len; i++) {
        Part *p = &g_array_index(parts, Part, i);
        double len = round(wd * CLAMP(p->value, 0, 100) / 100);
        if (at + len > x + wd) len = x + wd - at;
        set_colour(cr, w, p->colour, 1);
        cairo_rectangle(cr, at, y, len, ht);
        cairo_fill(cr);
        at += len;
    }
}

/* A memory stick: the board's outline with a key notch in its edge connector, the gold fingers, a row
   of chips, and the stacked bar of what the memory holds across the middle. */
static gboolean draw_dimm(GtkWidget *w, cairo_t *cr, gpointer d) {
    GArray *parts = g_object_get_data(G_OBJECT(w), "parts");
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    double x0 = 1.5, y0 = 1.5, x1 = W - 1.5, y1 = H - 1.5, fingers = 12, notch = W * 0.42;
    cairo_set_line_width(cr, 1);
    /* the board */
    set_colour(cr, w, "overlay", 0.55);
    cairo_rectangle(cr, x0, y0, x1 - x0, y1 - y0 - 4);
    cairo_fill(cr);
    set_colour(cr, w, "line", 1);
    cairo_move_to(cr, x0, y0);
    cairo_line_to(cr, x1, y0);
    cairo_line_to(cr, x1, y1 - 4);
    cairo_line_to(cr, notch + 5, y1 - 4);
    cairo_line_to(cr, notch + 5, y1);
    cairo_line_to(cr, notch - 5, y1);
    cairo_line_to(cr, notch - 5, y1 - 4);
    cairo_line_to(cr, x0, y1 - 4);
    cairo_close_path(cr);
    cairo_stroke(cr);
    /* the side notches */
    set_colour(cr, w, "base", 1);
    cairo_rectangle(cr, x0 - 1, (y0 + y1) / 2 - 4, 4, 8);
    cairo_rectangle(cr, x1 - 3, (y0 + y1) / 2 - 4, 4, 8);
    cairo_fill(cr);
    /* the gold fingers along the bottom edge, around the key notch */
    set_colour(cr, w, "amber", 0.55);
    for (double x = x0 + 6; x < x1 - 6; x += 4)
        if (x < notch - 7 || x > notch + 5) {
            cairo_rectangle(cr, x, y1 - 4 - fingers + 2, 2, fingers - 4);
            cairo_fill(cr);
        }
    /* the chips */
    int chips = 8;
    double cw = (x1 - x0 - 24) / chips;
    set_colour(cr, w, "line", 0.9);
    for (int i = 0; i < chips; i++) {
        cairo_rectangle(cr, x0 + 12 + i * cw + 2, y0 + 6, cw - 4, 8);
        cairo_fill(cr);
    }
    /* what it holds */
    stacked(cr, w, parts, x0 + 10, y0 + 20, x1 - x0 - 20, 14);
    return TRUE;
}

static gboolean draw_bar_parts(GtkWidget *w, cairo_t *cr, gpointer d) {
    stacked(cr, w, g_object_get_data(G_OBJECT(w), "parts"), 0, 0, gtk_widget_get_allocated_width(w),
            gtk_widget_get_allocated_height(w));
    return TRUE;
}

/* A GPU: the package with its pins on every side, the die in the middle holding a gauge per value
   (load, memory, power, fan…), each a column of segments lit to its level, its icon over it. */
static gboolean draw_chip(GtkWidget *w, cairo_t *cr, gpointer d) {
    JsonArray *meters = g_object_get_data(G_OBJECT(w), "meters");
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    guint n = meters ? json_array_get_length(meters) : 0;
    double px = 14, py = 6, pw = W - 2 * px, ph = H - 2 * py;   /* the package */
    cairo_set_line_width(cr, 1);
    set_colour(cr, w, "line", 0.8);   /* pins */
    for (double x = px + 10; x < px + pw - 8; x += 6) {
        cairo_rectangle(cr, x, py - 4, 2, 4);
        cairo_rectangle(cr, x, py + ph, 2, 4);
    }
    for (double y = py + 10; y < py + ph - 8; y += 6) {
        cairo_rectangle(cr, px - 4, y, 4, 2);
        cairo_rectangle(cr, px + pw, y, 4, 2);
    }
    cairo_fill(cr);
    set_colour(cr, w, "overlay", 0.45);
    cairo_rectangle(cr, px + 0.5, py + 0.5, pw - 1, ph - 1);
    cairo_fill_preserve(cr);
    set_colour(cr, w, "line", 1);
    cairo_stroke(cr);
    double dx = px + 16, dy = py + 10, dw = pw - 32, dh = ph - 20;   /* the die */
    set_colour(cr, w, "base", 0.9);
    cairo_rectangle(cr, dx + 0.5, dy + 0.5, dw - 1, dh - 1);
    cairo_fill_preserve(cr);
    set_colour(cr, w, "line", 1);
    cairo_stroke(cr);
    if (!n) return TRUE;
    double col = dw / n, top = dy + 26, bottom = dy + dh - 8;
    int segs = 6;
    double seg = (bottom - top) / segs;
    for (guint i = 0; i < n; i++) {
        JsonObject *m = json_array_get_object_element(meters, i);
        double v = CLAMP(jnum(m, "value"), 0, 100);
        const char *c = jstr(m, "color") ? jstr(m, "color") : level_colour(v);
        double cx = dx + i * col + col / 2, bw = MIN(col - 14, 26);
        /* its icon */
        PangoLayout *l = gtk_widget_create_pango_layout(w, jstr(m, "icon") ? jstr(m, "icon") : "");
        PangoRectangle ink;
        pango_layout_get_pixel_extents(l, &ink, NULL);
        set_colour(cr, w, "subtext", 1);
        cairo_move_to(cr, round(cx - ink.width / 2.0 - ink.x), round(dy + 14 - ink.height / 2.0 - ink.y));
        pango_cairo_show_layout(cr, l);
        g_object_unref(l);
        /* its gauge: segments from the bottom, lit up to the level */
        int lit = (int)round(v / 100 * segs);
        for (int s = 0; s < segs; s++) {
            set_colour(cr, w, s < lit ? c : "overlay", 1);
            cairo_rectangle(cr, round(cx - bw / 2), round(bottom - (s + 1) * seg + 1), round(bw), round(seg - 2));
            cairo_fill(cr);
        }
    }
    return TRUE;
}

static double *jvalues(JsonArray *a, guint *n) {
    *n = a ? json_array_get_length(a) : 0;
    double *v = g_new0(double, *n + 1);
    for (guint i = 0; i < *n; i++) v[i] = CLAMP(json_node_get_double(json_array_get_element(a, i)), 0, 100);
    return v;
}

/* a column per value, coloured by its level, its label under it */
static gboolean draw_bars(GtkWidget *w, cairo_t *cr, gpointer d) {
    JsonArray *vals = g_object_get_data(G_OBJECT(w), "values"), *labels = g_object_get_data(G_OBJECT(w), "labels");
    guint n;
    double *v = jvalues(vals, &n);
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    double label_h = labels ? 14 : 0, col = n ? (double)W / n : W, bw = MAX(4, MIN(col - 4, 18)), top = 2,
           bottom = H - label_h - 2;
    for (guint i = 0; i < n; i++) {
        double cx = i * col + col / 2, h = round((bottom - top) * v[i] / 100);
        set_colour(cr, w, "overlay", 1);
        cairo_rectangle(cr, round(cx - bw / 2), top, round(bw), bottom - top);
        cairo_fill(cr);
        set_colour(cr, w, level_colour(v[i]), 1);
        cairo_rectangle(cr, round(cx - bw / 2), bottom - h, round(bw), h);
        cairo_fill(cr);
        if (labels && i < json_array_get_length(labels)) {
            PangoLayout *l = gtk_widget_create_pango_layout(w, json_array_get_string_element(labels, i));
            PangoFontDescription *f = pango_font_description_copy(pango_context_get_font_description(pango_layout_get_context(l)));
            pango_font_description_set_absolute_size(f, 9 * PANGO_SCALE);
            pango_layout_set_font_description(l, f);
            pango_font_description_free(f);
            PangoRectangle ink;
            pango_layout_get_pixel_extents(l, NULL, &ink);
            set_colour(cr, w, "muted", 1);
            cairo_move_to(cr, round(cx - ink.width / 2.0), H - label_h + 1);
            pango_cairo_show_layout(cr, l);
            g_object_unref(l);
        }
    }
    g_free(v);
    return TRUE;
}

/* a history as a line with the area under it filled, a faint grid at 25 % steps */
static gboolean draw_spark(GtkWidget *w, cairo_t *cr, gpointer d) {
    guint n;
    double *v = jvalues(g_object_get_data(G_OBJECT(w), "values"), &n);
    int W = gtk_widget_get_allocated_width(w), H = gtk_widget_get_allocated_height(w);
    set_colour(cr, w, "overlay", 0.6);
    cairo_rectangle(cr, 0, 0, W, H);
    cairo_fill(cr);
    set_colour(cr, w, "line", 0.5);
    cairo_set_line_width(cr, 1);
    for (int g = 1; g < 4; g++) {
        cairo_move_to(cr, 0, round(H * g / 4.0) + 0.5);
        cairo_line_to(cr, W, round(H * g / 4.0) + 0.5);
    }
    cairo_stroke(cr);
    if (n >= 2) {
        double step = (double)W / (n - 1);
        cairo_move_to(cr, 0, H);
        for (guint i = 0; i < n; i++) cairo_line_to(cr, i * step, H - 1 - (H - 2) * v[i] / 100);
        cairo_line_to(cr, W, H);
        cairo_close_path(cr);
        set_colour(cr, w, "accent", 0.25);
        cairo_fill(cr);
        for (guint i = 0; i < n; i++) cairo_line_to(cr, i * step, H - 1 - (H - 2) * v[i] / 100);
        set_colour(cr, w, "accent", 1);
        cairo_set_line_width(cr, 1.5);
        cairo_stroke(cr);
    }
    g_free(v);
    return TRUE;
}

static GtkWidget *drawn(JsonObject *b, int height, GCallback draw) {
    GtkWidget *art = gtk_drawing_area_new();
    const char *keys[] = {"values", "labels"};
    for (int k = 0; k < 2; k++)
        if (json_object_has_member(b, keys[k]))
            g_object_set_data_full(G_OBJECT(art), keys[k], json_array_ref(json_object_get_array_member(b, keys[k])),
                                   (GDestroyNotify)json_array_unref);
    gtk_widget_set_size_request(art, 260, height);
    g_signal_connect(art, "draw", draw, NULL);
    add_class(art, "bartip-art");
    return art;
}

/* ---- the blocks ----------------------------------------------------------------------------------- */

static GtkWidget *head_block(JsonObject *b, gboolean section) {
    GtkWidget *row = hbox(7);
    if (jstr(b, "icon")) gtk_box_pack_start(GTK_BOX(row), glyph(jstr(b, "icon"), "bartip-icon", 0), FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(row), text(jstr(b, "title") ? jstr(b, "title") : "", "bartip-title"), FALSE, FALSE, 0);
    if (jstr(b, "detail")) gtk_box_pack_start(GTK_BOX(row), text(jstr(b, "detail"), "bartip-detail"), FALSE, FALSE, 0);
    if (jstr(b, "right")) {
        GtkWidget *r = coloured(jstr(b, "right"), "bartip-kv-value", jstr(b, "color"));
        gtk_label_set_xalign(GTK_LABEL(r), 1);
        gtk_box_pack_end(GTK_BOX(row), r, FALSE, FALSE, 0);
    }
    add_class(row, section ? "bartip-section" : "bartip-head");
    return row;
}

static GtkWidget *kv_block(JsonObject *b) {
    GtkWidget *grid = gtk_grid_new();
    gtk_grid_set_column_spacing(GTK_GRID(grid), 24);
    gtk_grid_set_row_spacing(GTK_GRID(grid), 2);
    add_class(grid, "bartip-kv");
    JsonArray *rows = json_object_has_member(b, "rows") ? json_object_get_array_member(b, "rows") : NULL;
    for (guint i = 0; rows && i < json_array_get_length(rows); i++) {
        JsonArray *r = json_array_get_array_element(rows, i);
        guint n = json_array_get_length(r);
        GtkWidget *w = text(n > 0 ? json_array_get_string_element(r, 0) : "", "bartip-kv-what");
        GtkWidget *v = coloured(n > 1 ? json_array_get_string_element(r, 1) : "", "bartip-kv-value",
                                n > 2 ? json_array_get_string_element(r, 2) : NULL);
        gtk_widget_set_hexpand(w, TRUE);
        gtk_label_set_xalign(GTK_LABEL(v), 1);
        gtk_grid_attach(GTK_GRID(grid), w, 0, i, 1, 1);
        gtk_grid_attach(GTK_GRID(grid), v, 1, i, 1, 1);
    }
    return grid;
}

static GtkWidget *meter_block(JsonObject *b) {
    double v = jnum(b, "value");
    GtkWidget *box = vbox(3), *line = hbox(10);
    gtk_box_pack_start(GTK_BOX(line), meter_widget(v, jstr(b, "color"), 6), TRUE, TRUE, 0);
    if (jstr(b, "right")) {
        GtkWidget *r = coloured(jstr(b, "right"), "bartip-kv-value", jstr(b, "color") ? jstr(b, "color") : level_colour(v));
        gtk_label_set_width_chars(GTK_LABEL(r), 4);
        gtk_label_set_xalign(GTK_LABEL(r), 1);
        gtk_box_pack_end(GTK_BOX(line), r, FALSE, FALSE, 0);
    }
    gtk_box_pack_start(GTK_BOX(box), line, FALSE, FALSE, 0);
    if (jstr(b, "left")) gtk_box_pack_start(GTK_BOX(box), text(jstr(b, "left"), "bartip-sub"), FALSE, FALSE, 0);
    add_class(box, "bartip-meterblock");
    return box;
}

static GtkWidget *stack_block(JsonObject *b) {
    GArray *parts = g_array_new(FALSE, TRUE, sizeof(Part));
    GtkWidget *box = vbox(8), *legend = hbox(14);
    JsonArray *segs = json_object_has_member(b, "segments") ? json_object_get_array_member(b, "segments") : NULL;
    for (guint i = 0; segs && i < json_array_get_length(segs); i++) {
        JsonObject *s = json_array_get_object_element(segs, i);
        Part p = {jnum(s, "value"), g_strdup(jstr(s, "color") ? jstr(s, "color") : "muted")};
        g_array_append_val(parts, p);
        GtkWidget *item = hbox(5), *swatch = meter_widget(100, p.colour, 8);
        gtk_widget_set_hexpand(swatch, FALSE);
        gtk_widget_set_size_request(swatch, 8, 8);
        gtk_box_pack_start(GTK_BOX(item), swatch, FALSE, FALSE, 0);
        gtk_box_pack_start(GTK_BOX(item), text(jstr(s, "label") ? jstr(s, "label") : "", "bartip-sub"), FALSE, FALSE, 0);
        gchar *pct = g_strdup_printf("%.0f%%", p.value);
        gtk_box_pack_start(GTK_BOX(item), text(pct, "bartip-value"), FALSE, FALSE, 0);
        g_free(pct);
        gtk_box_pack_start(GTK_BOX(legend), item, FALSE, FALSE, 0);
    }
    GtkWidget *art = gtk_drawing_area_new();
    g_object_set_data_full(G_OBJECT(art), "parts", parts, free_parts);
    gboolean dimm = !g_strcmp0(jstr(b, "style"), "dimm");
    gtk_widget_set_size_request(art, 260, dimm ? 64 : 8);
    g_signal_connect(art, "draw", G_CALLBACK(dimm ? draw_dimm : draw_bar_parts), NULL);
    gtk_box_pack_start(GTK_BOX(box), art, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), legend, FALSE, FALSE, 0);
    add_class(box, "bartip-art");
    return box;
}

static GtkWidget *chip_block(JsonObject *b) {
    GtkWidget *art = gtk_drawing_area_new();
    JsonArray *meters = json_object_has_member(b, "meters") ? json_object_get_array_member(b, "meters") : NULL;
    if (meters) g_object_set_data_full(G_OBJECT(art), "meters", json_array_ref(meters), (GDestroyNotify)json_array_unref);
    gtk_widget_set_size_request(art, 260, 130);
    add_class(art, "bartip-chip");
    g_signal_connect(art, "draw", G_CALLBACK(draw_chip), NULL);
    add_class(art, "bartip-art");
    return art;
}

static GtkWidget *table_block(JsonObject *b) {
    GtkWidget *grid = gtk_grid_new();
    gtk_grid_set_column_spacing(GTK_GRID(grid), 14);
    gtk_grid_set_row_spacing(GTK_GRID(grid), 2);
    add_class(grid, "bartip-kv");
    JsonArray *rows = json_object_has_member(b, "rows") ? json_object_get_array_member(b, "rows") : NULL;
    for (guint i = 0; rows && i < json_array_get_length(rows); i++) {
        JsonArray *r = json_array_get_array_element(rows, i);
        for (guint c = 0; c < json_array_get_length(r); c++) {
            GtkWidget *l = text(json_array_get_string_element(r, c), c ? "bartip-kv-value" : "bartip-body");
            if (c) gtk_label_set_xalign(GTK_LABEL(l), 1);
            else {
                gtk_widget_set_hexpand(l, TRUE);
                gtk_label_set_width_chars(GTK_LABEL(l), MIN(gtk_label_get_width_chars(GTK_LABEL(l)), 22));
                gtk_label_set_max_width_chars(GTK_LABEL(l), 22);
            }
            gtk_grid_attach(GTK_GRID(grid), l, c, i, 1, 1);
        }
    }
    return grid;
}

/* The card from the JSON; FALSE when it isn't one (then it's an ordinary tooltip). */
static gboolean build_rich(const char *markup) {
    if (!g_str_has_prefix(markup, "{\"bartip\"")) return FALSE;
    JsonParser *p = json_parser_new();
    if (!json_parser_load_from_data(p, markup, -1, NULL) || !JSON_NODE_HOLDS_OBJECT(json_parser_get_root(p))) {
        g_object_unref(p);
        return FALSE;
    }
    JsonObject *root = json_node_get_object(json_parser_get_root(p));
    JsonArray *blocks = json_object_has_member(root, "blocks") ? json_object_get_array_member(root, "blocks") : NULL;
    add_class(card, "bartip-rich");
    for (guint i = 0; blocks && i < json_array_get_length(blocks); i++) {
        JsonObject *b = json_array_get_object_element(blocks, i);
        const char *t = jstr(b, "type");
        GtkWidget *w = NULL;
        if (!g_strcmp0(t, "head")) w = head_block(b, FALSE);
        else if (!g_strcmp0(t, "section")) w = head_block(b, TRUE);
        else if (!g_strcmp0(t, "kv")) w = kv_block(b);
        else if (!g_strcmp0(t, "meter")) w = meter_block(b);
        else if (!g_strcmp0(t, "stack")) w = stack_block(b);
        else if (!g_strcmp0(t, "chip")) w = chip_block(b);
        else if (!g_strcmp0(t, "bars")) w = drawn(b, 54, G_CALLBACK(draw_bars));
        else if (!g_strcmp0(t, "spark")) w = drawn(b, 40, G_CALLBACK(draw_spark));
        else if (!g_strcmp0(t, "table")) w = table_block(b);
        else if (!g_strcmp0(t, "text")) w = coloured(jstr(b, "text"), "bartip-body", jstr(b, "color"));
        else if (!g_strcmp0(t, "rule")) add_rule(card);
        else if (!g_strcmp0(t, "hints") && jstr(b, "text")) add_footer(card, jstr(b, "text"));
        if (w) gtk_box_pack_start(GTK_BOX(card), w, FALSE, FALSE, 0);
    }
    g_object_unref(p);
    return TRUE;
}
