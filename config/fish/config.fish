source /usr/share/cachyos-fish-config/cachyos-config.fish

# The greeting (~/.local/bin/greet: a spinning bagel beside the system summary, any key skips it) only
# where it fits: in a narrow terminal, such as agentmux's terminals column or a VS Code kitty, it
# wraps into a mess, so those get just the prompt
function fish_greeting
    test $COLUMNS -ge 100; and ~/.local/bin/greet
end

# Omarchy prompt
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
set -gx PATH "/home/sami/.local/bin" $PATH
