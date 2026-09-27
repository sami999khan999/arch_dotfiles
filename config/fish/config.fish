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
