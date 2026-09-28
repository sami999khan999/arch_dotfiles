source /usr/share/cachyos-fish-config/cachyos-config.fish

# overwrite greeting
# potentially disabling fastfetch
#function fish_greeting
#    # smth smth
#end

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
