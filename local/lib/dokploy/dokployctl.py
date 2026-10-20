#!/usr/bin/env python3
# dokployctl.py — switch Dokploy (the self-hosted deploy dashboard, in Docker) on and off, and say what
# it's doing. `dokployctl` is its launcher; the Docker panel imports it for its Dokploy tab.
#
#   dokployctl status [--json]   on or off, its version, its services, the apps it deployed
#   dokployctl up                start Dokploy and the apps that were running when it went down
#   dokployctl down [--boot]     stop them all; nothing of it starts with Docker again until `up`
#
# Dokploy is Swarm services (dokploy, dokploy-postgres…), a Traefik container and the apps it deployed
# (compose projects under /etc/dokploy, or more Swarm services). Off = the services scaled to 0 and the
# containers stopped with a restart policy that doesn't bring them back: Docker remembers both, so a
# reboot doesn't start any of it. docker.service runs `down` as it stops and again as it starts
# (system/root-setup.sh), so Dokploy is never on after a reboot, a crash or Docker switched off.
import json, os, subprocess, sys

DATA = "/etc/dokploy"
CORE = ("dokploy", "dokploy-postgres", "dokploy-redis")   # Dokploy's own services, not apps
TRAEFIK = "dokploy-traefik"
# the dashboard's port: off the installer's 3000, which dev servers want (system/dokploy-setup.sh moves it)
PORT = 23300
URL = f"http://localhost:{PORT}"
# what `down` stopped, so `up` starts exactly that again
STATE = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "dokploy",
                     "stopped.json")


def docker(*args, timeout=60):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)


def lines(r):
    return [json.loads(l) for l in r.stdout.splitlines() if l.strip()] if r.returncode == 0 else []


def services():
    """Swarm services: [{id, name, mode, running, desired, image, core}]. [] without Swarm (no Dokploy)."""
    out = []
    for s in lines(docker("service", "ls", "--format", "{{json .}}", timeout=10)):
        running, _, desired = s.get("Replicas", "0/0").partition("/")
        out.append(dict(id=s["ID"], name=s["Name"], mode=s.get("Mode", ""), image=s.get("Image", ""), core=s["Name"] in CORE,
                        running=int(running or 0), desired=int(desired.split()[0] or 0) if desired else 0))
    return out


def app_containers():
    """The containers of the compose apps Dokploy deployed: their files are under /etc/dokploy."""
    fmt = ('{{.ID}}\t{{.Names}}\t{{.State}}\t{{.Label "com.docker.compose.project"}}'
           '\t{{.Label "com.docker.compose.project.working_dir"}}')
    out = []
    r = docker("ps", "-a", "--filter", "label=com.docker.compose.project", "--format", fmt, timeout=10)
    for line in r.stdout.splitlines() if r.returncode == 0 else []:
        cid, name, state, project, workdir = (line.split("\t") + [""] * 4)[:5]
        if workdir.startswith(DATA + "/"):
            out.append(dict(id=cid, name=name, state=state, project=project))
    return out


def traefik():
    """Traefik's state ('running', 'exited'…) or '' when there's no such container."""
    r = docker("inspect", "-f", "{{.State.Status}}", TRAEFIK, timeout=10)
    return r.stdout.strip() if r.returncode == 0 else ""


def version(svcs):
    core = next((s for s in svcs if s["name"] == "dokploy"), None)
    return core["image"].rpartition(":")[2] if core and ":" in core["image"] else ""


def status():
    svcs, apps = services(), app_containers()
    core = next((s for s in svcs if s["name"] == "dokploy"), None)
    projects = {}
    for c in apps:
        p = projects.setdefault(c["project"], dict(name=c["project"], running=0, total=0))
        p["total"] += 1
        p["running"] += c["state"] == "running"
    for s in svcs:
        if not s["core"]:   # an app deployed as a Swarm service (Dokploy's "Application")
            projects[s["name"]] = dict(name=s["name"], running=s["running"], total=max(s["desired"], s["running"], 1))
    return dict(installed=core is not None,
                on=bool(core and core["desired"]),
                ready=bool(core and core["running"]),
                version=version(svcs), url=URL, data=DATA, traefik=traefik(),
                services=svcs, apps=sorted(projects.values(), key=lambda p: p["name"]))


def load():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(state):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f, indent=1)


def down(boot=False):
    """Scale every Swarm service to 0 and stop Traefik and the apps. What was running is remembered
    (added to what an earlier `down` remembered: a second one, with nothing left running, keeps it)."""
    svcs = [s for s in services() if s["mode"] == "replicated" and s["desired"]]
    apps = [c for c in app_containers() if c["state"] in ("running", "restarting", "paused")]
    router = traefik() in ("running", "restarting")
    if not (svcs or apps or router):
        return "Dokploy is already off"
    state = load()
    state["services"] = state.get("services", {}) | {s["name"]: s["desired"] for s in svcs}
    state["containers"] = sorted(set(state.get("containers", [])) | {c["name"] for c in apps})
    save(state)
    stop = [c["id"] for c in apps] + ([TRAEFIK] if router else [])
    if stop:
        # "always" brings a stopped container back when Docker starts; "unless-stopped" leaves it stopped
        docker("update", "--restart", "unless-stopped", *stop)
    if svcs:
        docker("service", "scale", "--detach", *[f"{s['name']}=0" for s in svcs])
    if stop:
        docker("stop", "-t", "10", *stop, timeout=120)
    return f"Dokploy off{' (at Docker start)' if boot else ''}: {len(svcs)} services, {len(stop)} containers stopped"


def up():
    """Dokploy's services back to what they ran (its own at least 1), then Traefik and the apps."""
    svcs = services()
    if not any(s["name"] == "dokploy" for s in svcs):
        return "Dokploy isn't installed (system/dokploy-setup.sh)"
    state = load()
    want = state.get("services", {})
    scale = [f"{s['name']}={want.get(s['name']) or 1}" for s in svcs
             if s["mode"] == "replicated" and not s["desired"] and (s["core"] or s["name"] in want)]
    if scale:
        r = docker("service", "scale", "--detach", *scale)
        if r.returncode:
            return r.stderr.strip() or "docker service scale failed"
    names = set(state.get("containers", []))
    start = [TRAEFIK] + [c["name"] for c in app_containers() if c["name"] in names and c["state"] != "running"]
    docker("start", *start, timeout=120)
    save({})
    return f"Dokploy on: {len(scale)} services, {len(start)} containers starting · {URL}"


def main(argv):
    cmd = argv[0] if argv else "status"
    if cmd == "up":
        print(up())
    elif cmd == "down":
        print(down(boot="--boot" in argv))
    elif cmd == "status":
        st = status()
        if "--json" in argv:
            print(json.dumps(st, indent=1))
            return
        if not st["installed"]:
            print("Dokploy isn't installed (or Docker is off)")
            return
        print(f"Dokploy {st['version']}: {'on' if st['on'] else 'off'}{'' if st['ready'] or not st['on'] else ' (starting)'}"
              f" · {st['url']}")
        for s in st["services"]:
            print(f"  {'core' if s['core'] else 'app '}  {s['name']:<28} {s['running']}/{s['desired']}")
        print(f"  core  {TRAEFIK:<28} {st['traefik'] or 'missing'}")
        for a in st["apps"]:
            if a["name"] not in {s["name"] for s in st["services"]}:
                print(f"  app   {a['name']:<28} {a['running']}/{a['total']}")
    else:
        print("usage: dokployctl status [--json] | up | down [--boot]", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main(sys.argv[1:])
