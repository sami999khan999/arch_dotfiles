-- Workspace rules wiki https://wiki.hypr.land/Configuring/Basics/Workspace-Rules/
-- Workspaces 1..10 are created by wsgroups.lua (always shown in waybar); only extras go here.
-- Add your workspace rules here. Increment the workspace number as you go. Do not have duplicate workspaces.
hl.workspace_rule({ workspace = "name:gaming", monitor = PRIMARY_MONITOR, default = true })
-- 10 is the Control Center: no outer gap, its cards bring their own spacing (modules/controlcenter.lua)
hl.workspace_rule({ workspace = "10", monitor = MONITOR1, default = true, persistent = true, gaps_out = 0 })
-- hl.workspace_rule({ workspace = "4", monitor = MONITOR2, default = true, persistent = true })
-- hl.workspace_rule({ workspace = "5", monitor = MONITOR2, default = true, persistent = true })
-- hl.workspace_rule({ workspace = "6", monitor = MONITOR2, default = true, persistent = true })

-- For other layouts such as scrolling, see example below
-- hl.workspace_rule({ workspace = "1", monitor = MONITOR1, default = true, persistent = true, layout = "scrolling" })
