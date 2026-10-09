source /usr/share/cachyos-fish-config/cachyos-config.fish

# The greeting: ~/.local/bin/greet, a spinning bagel beside the system summary (any key skips it). It
# fits itself to the terminal: wide gets the bagel, medium the summary alone, narrow (agentmux's
# terminals column, a VS Code kitty) nothing, just the prompt. agentmux's terminals never get it
# (AGENTMUX_TERM, set on their session): a new one opens clean
function fish_greeting
    set -q AGENTMUX_TERM; or ~/.local/bin/greet
end

# Omarchy prompt (in the colour theme's colours: local/bin/theme makes this file, Tokyo Night's own when it's active)
if test -f ~/.config/themes/current/starship.toml
    set -gx STARSHIP_CONFIG ~/.config/themes/current/starship.toml
end
if status is-interactive; and type -q starship
    starship init fish | source
end

# Dev toolchains: rustup's cargo bin, mise-managed node/pnpm/bun, zoxide (`z dir`)
fish_add_path -g ~/.cargo/bin
if type -q mise
    mise activate fish | source
end
if status is-interactive; and type -q zoxide
    zoxide init fish | source
end

# Ctrl+Backspace deletes the word before the cursor the way a text editor (VS Code) does: spaces,
# then either a run of letters/digits/_ or a run of punctuation ("foo_bar" goes whole, "a/b/c" one
# part at a time). Also bound to ctrl-h: in tmux (agentmux's terminals, the VS Code kittys) the key
# arrives as ^H. Backspace itself is ^?, untouched.
function _editor_backward_kill_word
    set -l pos (commandline -C)
    set -l before (string sub -l $pos -- (string join \n -- (commandline)))
    set -l cut (string match -r -- '(?:\w+|[^\w\s]+)?\s*$' $before)
    set -l n (string length -- "$cut")
    test $n -gt 0; or return
    set -l after (string sub -s (math $pos + 1) -- (string join \n -- (commandline)))
    set -l kept (string sub -l (math $pos - $n) -- "$before")
    commandline -r -- "$kept$after"
    commandline -C (math $pos - $n)
end

# In fish_user_key_bindings: fish loads its default bindings after config.fish, wiping a plain bind.
function fish_user_key_bindings
    bind ctrl-backspace _editor_backward_kill_word
    bind ctrl-h _editor_backward_kill_word
end


# Added by Antigravity CLI installer
fish_add_path -gm ~/.local/bin   # first, once: a plain prepend added it again in every nested shell

# >>> railway initialize >>>
# only where the Railway CLI is installed: another PC without it would print an error in every shell
test -f "$HOME/.railway/env.fish"; and source "$HOME/.railway/env.fish"
# <<< railway initialize <<<
