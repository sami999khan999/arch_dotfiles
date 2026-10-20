This PC · sami's CachyOS desktop

# Putting a site on the internet with Dokploy and Tailscale

How a website running on this computer can be opened by anyone, from anywhere, without opening your router or your firewall.

**In one sentence:** Dokploy builds and runs your website inside Docker on this PC, and a small Tailscale container next to it opens a secure tunnel *out* to Tailscale, whose Funnel feature gives the site a public https://….ts.net address and sends visitors back down that tunnel.

What I found

## How this PC is set up today

I checked the running system and the repo on 19 October 2026.

ready

**Dokploy v0.30.8 is on.** Dashboard at localhost:3000, with its database and Traefik (the web router) on ports 80 and 443.

ready

**One app is deployed:** video-grabber, listening on port 7845. Today it only answers at video-grabber.localhost, so only this PC can open it.

ready

**The network is closed.** The Docker firewall rule drops every new connection from outside into a container. Your phone on Wi-Fi can't reach these sites, and nor can the internet.

ready

**A Tailscale auth key is saved** in \~/.local/share/dokploy/tailscale-authkey (private, not in git). The manual's plan is to give it to each app as TS_AUTHKEY.

not yet

**No Tailscale container is running yet,** so no app is on the internet. Tailscale is not installed on the PC itself either, and it doesn't need to be: it runs inside Docker, next to each app.

blocked

**The normal way would not work here.** The PC sits behind a home router (192.168.0.5 inside, a shared public address outside). Opening ports 80 and 443 on the router would also undo the closed firewall. Tailscale avoids both.

The picture

## The path a visitor takes

The blue line is the only way in. The Tailscale container dials out to Tailscale (outgoing traffic is allowed), and visitors come back along that same line. A direct visit to your home address hits the closed wall.

Who does what

## Four parts, four jobs

### Dokploy

Takes your code from Git, builds it into a Docker image, runs it, and restarts it if it crashes. Redeploy with one click.

Like a builder who also looks after the house.

### Your app

The website itself, in its own container. It only talks to things on this PC.

The house.

### Tailscale container

Logs in to your Tailscale account with the auth key and keeps an encrypted connection open to Tailscale. It passes each request on to your app.

A private phone line from the house to the outside.

### Tailscale Funnel

Tailscale's public servers. They own the .ts.net address, handle the HTTPS certificate, and forward visitors down your line.

The public front desk that answers the phone for you.

Doing it

## Publishing a site, step by step

1. ### Prepare Tailscale once (in its web admin)

   Turn on **MagicDNS** and **HTTPS certificates**. In the access rules, create the tag tag:app, allow it to use Funnel, and give it no access to your other devices. Make the auth key for tag:app (you already saved one).
2. ### Start Dokploy

   It is off after every reboot on purpose. Use the Dokploy window on workspace 9, or run `dokployctl up`.
3. ### Deploy the app in Dokploy

   Point Dokploy at the Git repo. video-grabber is already done.
4. ### Add the Tailscale container next to it

   In Dokploy, create a small **Compose** service with the Tailscale image, on the same Docker network as the app. Give it the auth key as TS_AUTHKEY, a name (this becomes the web address), and a short "serve" file that says: *send public visitors to the app on port 7845*. The ready-to-use file is below.
5. ### Tell the app its new name

   video-grabber is a Django app with an ALLOWED_HOSTS setting. Add video-grabber.your-tailnet.ts.net to it in Dokploy, or Django will refuse the visitors.
6. ### Open the address

   After a minute, https://video-grabber.your-tailnet.ts.net works from any phone or computer, with a valid certificate. "your-tailnet" is the name Tailscale shows in its admin page.

The Compose file for step 4

Paste into a new Compose service in Dokploy. Set TS_AUTHKEY in its Environment tab, and add serve.json under Advanced → Mounts.

```
services:
  tailscale:
    image: tailscale/tailscale:stable
    hostname: video-grabber            # becomes video-grabber.<tailnet>.ts.net
    environment:
      - TS_AUTHKEY=${TS_AUTHKEY}
      - TS_EXTRA_ARGS=--advertise-tags=tag:app
      - TS_STATE_DIR=/var/lib/tailscale
      - TS_SERVE_CONFIG=/config/serve.json
    volumes:
      - ts-state:/var/lib/tailscale    # keeps the same name after a restart
      - ../files/serve.json:/config/serve.json:ro
    networks: [dokploy-network]
    restart: unless-stopped

volumes:
  ts-state:

networks:
  dokploy-network:
    external: true
```

```
// serve.json: public HTTPS on 443, passed to the app
{
  "TCP": { "443": { "HTTPS": true } },
  "Web": {
    "${TS_CERT_DOMAIN}:443": {
      "Handlers": { "/": { "Proxy": "http://video-grabber-3isi9n:7845" } }
    }
  },
  "AllowFunnel": { "${TS_CERT_DOMAIN}:443": true }
}
```

video-grabber-3isi9n is the app's name inside Docker, as Dokploy named it. For another app, change that name, the port and the hostname.

Good to know

## What stays safe, and the limits

- **Nothing is opened.** No router port forwarding, and the Docker firewall stays closed. The only public thing is the one address Funnel serves.
- **The Tailscale container can't reach your other devices.** The tag:app rule lets it use Funnel and nothing else, so a hacked app can't reach your phone or laptop over Tailscale.
- **The dashboard stays private.** Dokploy on port 3000 is not published. Only the app you point Funnel at is.
- **The site is only up while this PC is.** If the PC sleeps, shuts down, or Dokploy is off (its default after a reboot), visitors get an error.
- **The address ends in .ts.net.** Funnel doesn't serve your own domain name. For mysite.com you would need a different tunnel (for example Cloudflare Tunnel) or a server.
- **It suits small sites.** Speed is limited by your home upload and by Tailscale's relays. It is right for demos, personal tools and showing clients, not for heavy traffic.

Words used here

## Short glossary

Container

A program packed with everything it needs, running separately from the rest of the PC.

Traefik

Dokploy's built-in router. It sends \*.localhost names to the right app on this PC. Funnel doesn't need it.

Tailnet

Your private Tailscale network, with a name like tail1234.ts.net.

Auth key

A password the Tailscale container uses to join your tailnet without you logging in.

Funnel

Tailscale's feature that lets the public internet reach one service on your tailnet.

Tag

A label (tag:app) that decides what a device on your tailnet may do.
