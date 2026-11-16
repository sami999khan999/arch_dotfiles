/* media.c — the player's card (waybar's mpris module, #mpris), included by bartips.c.
 *
 * Instead of the tooltip's text: the player's name and state, the cover, the title and artist, a seek
 * bar (a knob under the pointer: click or drag), the time, and previous / play-pause / next buttons
 * (previous restarts the track when it's past 3 s, or the player has none). YouTube's next / previous
 * video come through Chrome's MPRIS thanks to the Desktop Companion extension (downloads/chrome/youtube.js); scrolling the card skips
 * tracks, as scrolling the icon does. It talks to the player straight
 * over MPRIS (D-Bus): the one the module shows (its tooltip's "{player} · {status}" line), a playing one
 * first. While the card is shown it reads the position once a second; nothing runs once it hides.
 */

#define MPRIS_PATH "/org/mpris/MediaPlayer2"
#define MPRIS_PLAYER "org.mpris.MediaPlayer2.Player"
#define COVER 56   /* px: the cover's square */

static struct {
    GDBusConnection *bus;
    gchar *name;          /* the player's bus name: org.mpris.MediaPlayer2.chromium.instance4808 */
    gchar *player;        /* what it calls itself (Identity): "Chromium" */
    gchar *track;         /* its track id, which SetPosition needs */
    gchar *title, *artist, *art;
    gchar *shown;         /* the player and track the card was built for: another one rebuilds it */
    gint64 len, pos;      /* µs */
    gboolean playing, can_seek, can_next, can_prev;
    gboolean dragging;          /* the seek bar's knob is held */
    double drag;                /* where it's being dragged to, 0-1 */
    GtkWidget *seek, *at, *play, *icon, *state;   /* weak: gone when the card is rebuilt */
    guint tick;
} media;

static GVariant *mpris_props(const char *name, const char *iface) {
    GVariant *r = g_dbus_connection_call_sync(media.bus, name, MPRIS_PATH, "org.freedesktop.DBus.Properties",
                                              "GetAll", g_variant_new("(s)", iface), G_VARIANT_TYPE("(a{sv})"),
                                              G_DBUS_CALL_FLAGS_NONE, 300, NULL, NULL);   /* a hung player can't hang the bar */
    if (!r) return NULL;
    GVariant *d = g_variant_get_child_value(r, 0);
    g_variant_unref(r);
    return d;
}

static const char *vstr(GVariant *d, const char *key) {
    const char *s = NULL;
    return d && g_variant_lookup(d, key, "&s", &s) ? s : NULL;
}

static gboolean vbool(GVariant *d, const char *key) {
    gboolean b = FALSE;
    return d && g_variant_lookup(d, key, "b", &b) && b;
}

/* a number whatever its D-Bus type: players send the length as x, t or i */
static gint64 vint(GVariant *d, const char *key) {
    GVariant *v = d ? g_variant_lookup_value(d, key, NULL) : NULL;
    gint64 n = 0;
    if (!v) return 0;
    if (g_variant_is_of_type(v, G_VARIANT_TYPE_INT64)) n = g_variant_get_int64(v);
    else if (g_variant_is_of_type(v, G_VARIANT_TYPE_UINT64)) n = (gint64)g_variant_get_uint64(v);
    else if (g_variant_is_of_type(v, G_VARIANT_TYPE_INT32)) n = g_variant_get_int32(v);
    else if (g_variant_is_of_type(v, G_VARIANT_TYPE_DOUBLE)) n = (gint64)g_variant_get_double(v);
    g_variant_unref(v);
    return n;
}

/* the module's player, from its tooltip ("chromium · playing"): the first word before " · " */
static gchar *shown_player(const char *markup) {
    gchar **lines = g_strsplit(markup ? markup : "", "\n", -1), *hit = NULL;
    for (gchar **ln = lines; *ln && !hit; ln++) {
        gchar *t = plain(*ln), *dot = strstr(t, " · ");
        if (dot && dot > t && !memchr(t, ' ', dot - t)) hit = g_strndup(t, dot - t);
        g_free(t);
    }
    g_strfreev(lines);
    return hit;
}

/* Read the player: the module's own (one that plays first), else any that plays, else any. */
static gboolean media_read(const char *markup) {
    if (!media.bus) media.bus = g_bus_get_sync(G_BUS_TYPE_SESSION, NULL, NULL);
    if (!media.bus) return FALSE;
    GVariant *r = g_dbus_connection_call_sync(media.bus, "org.freedesktop.DBus", "/org/freedesktop/DBus",
                                              "org.freedesktop.DBus", "ListNames", NULL, G_VARIANT_TYPE("(as)"),
                                              G_DBUS_CALL_FLAGS_NONE, 300, NULL, NULL);
    if (!r) return FALSE;
    gchar *want = shown_player(markup);
    gchar *prefix = want ? g_strconcat("org.mpris.MediaPlayer2.", want, NULL) : NULL;
    GVariantIter *it;
    const char *n;
    gchar *best = NULL;
    int best_score = -1;
    GVariant *best_props = NULL;
    g_variant_get(r, "(as)", &it);
    while (g_variant_iter_loop(it, "&s", &n)) {
        if (!g_str_has_prefix(n, "org.mpris.MediaPlayer2.")) continue;
        GVariant *p = mpris_props(n, MPRIS_PLAYER);
        if (!p) continue;
        size_t pl = prefix ? strlen(prefix) : 0;
        gboolean ours = prefix && g_str_has_prefix(n, prefix) && (n[pl] == 0 || n[pl] == '.');
        const char *st = vstr(p, "PlaybackStatus");
        int score = (ours ? 4 : 0) + (!g_strcmp0(st, "Playing") ? 2 : !g_strcmp0(st, "Paused") ? 1 : 0);
        if (score > best_score) {
            g_free(best);
            if (best_props) g_variant_unref(best_props);
            best = g_strdup(n); best_props = p; best_score = score;
        } else {
            g_variant_unref(p);
        }
    }
    g_variant_iter_free(it);
    g_variant_unref(r);
    g_free(want); g_free(prefix);
    if (!best) return FALSE;

    g_free(media.name); media.name = best;
    GVariant *ident = mpris_props(best, "org.mpris.MediaPlayer2");
    g_free(media.player);
    media.player = g_strdup(vstr(ident, "Identity") ? vstr(ident, "Identity") : best + strlen("org.mpris.MediaPlayer2."));
    if (ident) g_variant_unref(ident);

    media.playing = !g_strcmp0(vstr(best_props, "PlaybackStatus"), "Playing");
    media.can_seek = vbool(best_props, "CanSeek");
    media.can_next = vbool(best_props, "CanGoNext");
    media.can_prev = vbool(best_props, "CanGoPrevious");
    media.pos = vint(best_props, "Position");
    GVariant *meta = g_variant_lookup_value(best_props, "Metadata", G_VARIANT_TYPE_VARDICT);
    media.len = vint(meta, "mpris:length");
    g_free(media.title); g_free(media.artist); g_free(media.art); g_free(media.track);
    media.title = g_strdup(vstr(meta, "xesam:title"));
    media.art = g_strdup(vstr(meta, "mpris:artUrl"));
    media.track = NULL;
    GVariant *tid = meta ? g_variant_lookup_value(meta, "mpris:trackid", NULL) : NULL;
    if (tid && (g_variant_is_of_type(tid, G_VARIANT_TYPE_OBJECT_PATH) || g_variant_is_of_type(tid, G_VARIANT_TYPE_STRING)))
        media.track = g_variant_dup_string(tid, NULL);
    if (tid) g_variant_unref(tid);
    GVariant *artists = meta ? g_variant_lookup_value(meta, "xesam:artist", G_VARIANT_TYPE_STRING_ARRAY) : NULL;
    media.artist = NULL;
    if (artists) {
        const gchar **a = g_variant_get_strv(artists, NULL);
        media.artist = a && a[0] && *a[0] ? g_strjoinv(", ", (gchar **)a) : NULL;
        g_free(a);
        g_variant_unref(artists);
    }
    if (meta) g_variant_unref(meta);
    g_variant_unref(best_props);
    return TRUE;
}

static gchar *media_key(void) {
    return g_strdup_printf("%s\n%s\n%s\n%s", media.name, media.track ? media.track : "",
                           media.title ? media.title : "", media.artist ? media.artist : "");
}

static gchar *clock_text(gint64 us) {
    gint64 s = MAX(us, 0) / G_USEC_PER_SEC;
    return s >= 3600 ? g_strdup_printf("%d:%02d:%02d", (int)(s / 3600), (int)(s / 60 % 60), (int)(s % 60))
                     : g_strdup_printf("%d:%02d", (int)(s / 60), (int)(s % 60));
}

/* the parts that move: the position, the bar, the play / pause glyph */
static void media_show_state(void) {
    if (media.seek) slider_set(media.seek, media.len > 0 ? CLAMP((double)media.pos / media.len, 0, 1) : 0);
    if (media.at) {   /* while dragging: where the knob would go */
        gchar *a = clock_text(media.dragging ? (gint64)(media.drag * media.len) : media.pos), *b = media.len > 0 ? clock_text(media.len) : g_strdup("–:––");
        gchar *s = g_strdup_printf("%s / %s", a, b);
        gtk_label_set_text(GTK_LABEL(media.at), s);
        g_free(a); g_free(b); g_free(s);
    }
    GtkWidget *glyphs[] = {media.play, media.icon};   /* the button and the header: as on the bar's icon */
    for (int i = 0; i < 2; i++) {
        if (!glyphs[i]) continue;
        g_object_set_data_full(G_OBJECT(glyphs[i]), "glyph",
                               g_strdup(media.playing ? "\U000F03E4" : "\U000F040A"), g_free);   /* 󰏤 / 󰐊 */
        gtk_widget_queue_draw(glyphs[i]);
    }
    if (media.state) gtk_label_set_text(GTK_LABEL(media.state), media.playing ? "playing" : "paused");
}

/* the tooltip changed, or a second went by: the same track updates in place, another rebuilds the card */
static void media_update(void) {
    gchar *m = source ? gtk_widget_get_tooltip_markup(source) : NULL;
    gboolean ok = media_read(m);
    g_free(m);
    if (!ok) { rebuild(); return; }
    gchar *key = media_key();
    gboolean same = !g_strcmp0(key, media.shown);
    g_free(key);
    if (same || media.dragging) media_show_state();   /* never rebuilt under a drag */
    else rebuild();
}

static gboolean media_tick(gpointer d) {
    if (!popup || !gtk_widget_get_visible(popup) || kind != MEDIA) { media.tick = 0; return G_SOURCE_REMOVE; }
    media_update();
    return G_SOURCE_CONTINUE;
}

static gboolean media_soon(gpointer d) {   /* a moment after a button: the player has answered */
    if (popup && gtk_widget_get_visible(popup) && kind == MEDIA) media_update();
    return G_SOURCE_REMOVE;
}

static void media_call(const char *method, GVariant *args) {
    if (!media.bus || !media.name) return;
    g_dbus_connection_call(media.bus, media.name, MPRIS_PATH, MPRIS_PLAYER, method, args, NULL,
                           G_DBUS_CALL_FLAGS_NONE, 1000, NULL, NULL, NULL);
    g_timeout_add(150, media_soon, NULL);
}

static void media_previous(void);

static gboolean media_button(GtkWidget *w, GdkEventButton *e, gpointer method) {
    if (e->button != 1) return FALSE;
    if (!strcmp(method, "Previous")) media_previous();
    else media_call(method, NULL);
    return TRUE;
}

/* the seek bar: the time follows the knob while it's dragged; the player seeks once, on release */
static void seek_moved(GtkWidget *w, double f, gboolean done) {
    media.drag = f;
    media.dragging = !done;
    if (done) {
        media.pos = (gint64)(f * media.len);
        if (media.track) media_call("SetPosition", g_variant_new("(ox)", media.track, media.pos));
    }
    media_show_state();
}

/* previous: back to the start, when it's past the first seconds or the player has no previous (a
   single video in a browser) — as music players do */
static void media_previous(void) {
    if (media.can_seek && media.track && (!media.can_prev || media.pos > 3 * G_USEC_PER_SEC)) {
        media.pos = 0;
        media_show_state();
        media_call("SetPosition", g_variant_new("(ox)", media.track, (gint64)0));
    } else if (media.can_prev) {
        media_call("Previous", NULL);
    }
}

/* scrolling the card: next / previous, as on the icon (config.jsonc: up is next) */
static void media_scroll(int step) {
    if (step > 0) media_previous();
    else if (media.can_next) media_call("Next", NULL);
}

static GtkWidget *media_btn(const char *icon, const char *method, gboolean can, gboolean main) {
    GtkWidget *eb = gtk_event_box_new(), *tile = glyph(icon, "bartip-tile", TILE);
    add_class(eb, "bartip-btn");
    if (main) add_class(eb, "main");
    gtk_container_add(GTK_CONTAINER(eb), tile);
    if (can) {
        gtk_widget_add_events(eb, GDK_BUTTON_PRESS_MASK | GDK_BUTTON_RELEASE_MASK | GDK_ENTER_NOTIFY_MASK |
                                  GDK_LEAVE_NOTIFY_MASK);
        g_signal_connect(eb, "button-release-event", G_CALLBACK(media_button), (gpointer)method);
        g_signal_connect(eb, "enter-notify-event", G_CALLBACK(row_cross), NULL);
        g_signal_connect(eb, "leave-notify-event", G_CALLBACK(row_cross), NULL);
    } else {
        add_class(eb, "off");
    }
    return eb;
}

/* the cover, cropped to a square (a video's thumbnail is wide); only a local file: nothing downloads */
static GtkWidget *media_cover(void) {
    gchar *path = media.art && g_str_has_prefix(media.art, "file://") ? g_filename_from_uri(media.art, NULL, NULL) : NULL;
    GdkPixbuf *pb = path ? gdk_pixbuf_new_from_file(path, NULL) : NULL;
    g_free(path);
    if (!pb) return NULL;
    int w = gdk_pixbuf_get_width(pb), h = gdk_pixbuf_get_height(pb), side = MIN(w, h);
    GdkPixbuf *sq = gdk_pixbuf_new_subpixbuf(pb, (w - side) / 2, (h - side) / 2, side, side);
    GdkPixbuf *small = gdk_pixbuf_scale_simple(sq, COVER, COVER, GDK_INTERP_BILINEAR);
    GtkWidget *img = gtk_image_new_from_pixbuf(small);
    g_object_unref(small); g_object_unref(sq); g_object_unref(pb);
    add_class(img, "bartip-cover");
    gtk_widget_set_valign(img, GTK_ALIGN_START);
    return img;
}

static gboolean build_media(const char *markup) {
    if (!media_read(markup)) return FALSE;
    add_class(card, "bartip-media");
    g_free(media.shown);
    media.shown = media_key();

    GtkWidget *h = hbox(7), *icon = glyph("\U000F03E4", "bartip-icon", 0), *state = text("", "bartip-detail");
    GtkWidget *who = text("", "bartip-title");
    gtk_label_set_text(GTK_LABEL(who), media.player);
    gtk_box_pack_start(GTK_BOX(h), icon, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(h), who, FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(h), state, FALSE, FALSE, 0);
    add_class(h, "bartip-head");
    gtk_box_pack_start(GTK_BOX(card), h, FALSE, FALSE, 0);

    /* "twenty one pilots - Hometown" by twenty one pilots: the title without the artist again */
    const char *title = media.title && *media.title ? media.title : "Unknown title";
    if (media.artist && g_str_has_prefix(title, media.artist) && g_str_has_prefix(title + strlen(media.artist), " - ")
        && title[strlen(media.artist) + 3])
        title += strlen(media.artist) + 3;
    GtkWidget *top = hbox(11), *words = vbox(2), *cover = media_cover();
    if (cover) gtk_box_pack_start(GTK_BOX(top), cover, FALSE, FALSE, 0);
    GtkWidget *t = text(title, "bartip-title");
    gtk_label_set_use_markup(GTK_LABEL(t), FALSE);
    gtk_label_set_text(GTK_LABEL(t), title);   /* a song title is text, never markup */
    gtk_label_set_max_width_chars(GTK_LABEL(t), 34);
    gtk_label_set_width_chars(GTK_LABEL(t), MIN(gtk_label_get_width_chars(GTK_LABEL(t)), 34));
    gtk_box_pack_start(GTK_BOX(words), t, FALSE, FALSE, 0);
    if (media.artist) {
        GtkWidget *a = text("", "bartip-body");
        gtk_label_set_text(GTK_LABEL(a), media.artist);
        gtk_label_set_max_width_chars(GTK_LABEL(a), 34);
        gtk_box_pack_start(GTK_BOX(words), a, FALSE, FALSE, 0);
    }
    gtk_widget_set_valign(words, GTK_ALIGN_CENTER);
    gtk_box_pack_start(GTK_BOX(top), words, TRUE, TRUE, 0);
    gtk_box_pack_start(GTK_BOX(card), top, FALSE, FALSE, 0);

    GtkWidget *bar = slider(0, media.can_seek && media.len > 0, seek_moved);
    add_class(bar, "bartip-seek");
    media.dragging = FALSE;
    gtk_box_pack_start(GTK_BOX(card), bar, FALSE, FALSE, 0);

    GtkWidget *controls = hbox(6), *at = text("", "bartip-time");
    add_class(controls, "bartip-controls");
    gtk_box_pack_start(GTK_BOX(controls), at, FALSE, FALSE, 0);
    GtkWidget *play = media_btn(media.playing ? "\U000F03E4" : "\U000F040A", "PlayPause", TRUE, TRUE);
    gtk_box_pack_end(GTK_BOX(controls), media_btn("\U000F04AD", "Next", media.can_next, FALSE), FALSE, FALSE, 0);   /* 󰒭 */
    gtk_box_pack_end(GTK_BOX(controls), play, FALSE, FALSE, 0);
    gtk_box_pack_end(GTK_BOX(controls), media_btn("\U000F04AE", "Previous", media.can_prev || media.can_seek, FALSE), FALSE, FALSE, 0);   /* 󰒮 */
    gtk_widget_set_valign(at, GTK_ALIGN_CENTER);
    gtk_box_pack_start(GTK_BOX(card), controls, FALSE, FALSE, 0);

    g_set_weak_pointer(&media.seek, bar);
    g_set_weak_pointer(&media.at, at);
    g_set_weak_pointer(&media.icon, icon);
    g_set_weak_pointer(&media.state, state);
    g_set_weak_pointer(&media.play, gtk_bin_get_child(GTK_BIN(play)));
    media_show_state();
    add_footer(card, "Scroll: next / previous");
    if (!media.tick) media.tick = g_timeout_add(1000, media_tick, NULL);
    return TRUE;
}
