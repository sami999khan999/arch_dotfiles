-- Control Center (workspace 10): one kitty window running scripts/controlcenter.py, which draws
-- the five cards itself on one character grid (so all the gaps between them are equal).
-- The window gets no border of its own and fills the workspace edge to edge (gaps_out = 0 in
-- workspaces.lua); kitty/controlcenter.conf's padding sets the margin around the cards.
hl.window_rule({
    match       = { class = "^(controlcenter)$" },
    border_size = 0,
})
