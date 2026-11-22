[README.md](https://github.com/user-attachments/files/35238543/README.md)
# Plateful

A self-hosted, fully offline weekly meal planner. Python standard library only, with no pip packages, no internet calls and no accounts.

- **Week-by-week planner.** Each week has its own budget, its own list of who's eating, any guests, and its own set of meals (breakfast, lunch, dinner, snack, per day).
- **Household editor.** Add or remove people at any time. Set diets, allergies, likes and dislikes, and a portion size for each person.
- **Budget-aware suggestions.** "Fill empty meals" respects diets and allergies, learns from 👍/👎, avoids repeats, reuses ingredients across meals, and fits your budget. Lock any meal, then regenerate the rest.
- **Grocery list.** Grouped by aisle, checkable on your phone, with pantry items excluded and extras you add yourself.
- **Price Book.** Your own prices for your store. The starter prices are estimates, so edit them or import a CSV.
- **Recipes.** About 100 starter recipes (original, family-style). You can add your own or import a single recipe from a web page (Import from URL), then review it before saving.
- **Meijer shopping mode.** In Groceries, "Start shopping" steps through your list one item at a time, opening each item's Meijer search so you can tap Add to cart. Items are checked off as you go. There's also Copy list and Share. Meijer has no public cart API, so this can't fill your cart automatically.
- **Phone-friendly.** Bottom tab bar, large touch targets and slide-up sheets on phones.
- **Accounts.** Sign-in with admin and member roles, and a dark theme with an optional light mode.

## Install on Proxmox (helper script)

Run these on the **Proxmox node shell**. The script creates a Debian 12 LXC (1 core, 512 MB RAM, 4 GB disk), installs Plateful as a systemd service, and prints the URL.

**Option A: from GitHub.** Push this folder to a repo, then run:

```bash
PLATEFUL_REPO=youruser/plateful bash -c "$(curl -fsSL https://raw.githubusercontent.com/youruser/plateful/main/install/plateful.sh)"
```

**Option B: no GitHub.** Copy `plateful.tar.gz` and `install/plateful.sh` to the node (WinSCP or scp), then run:

```bash
PLATEFUL_TARBALL=/root/plateful.tar.gz bash /root/plateful.sh
```

Open `http://<container-ip>:8080`. The first launch runs the setup wizard.

Update later with `PLATEFUL_TARBALL=... bash plateful.sh update <CTID>`. Your data is kept.

Overrides: `CTID`, `CT_NAME`, `CORES`, `RAM`, `DISK`, `BRIDGE`, `STORAGE`, `TEMPLATE_STORAGE`, `PORT`.

## Accounts and exposing it to the internet

- The first visit asks you to create the **admin account**. Do this right after installing, before the server is reachable by anyone else.
- To add family: **Settings → Invite family → Create invite**. Send them the link. They choose their own username and password and join as a member. Invites work once and expire after 7 days. Admins can also create accounts directly. There is no open signup, so only people you invite can join.
- Passwords are stored as salted PBKDF2 hashes. Sessions last 30 days, logins are rate-limited, and the cookie is HttpOnly + SameSite.
- **Do not port-forward the plain HTTP port.** Passwords would cross the internet unencrypted. Put it behind HTTPS instead:
  - **Cloudflare Tunnel** (no open ports), or
  - a reverse proxy with a certificate (Nginx Proxy Manager, Caddy, Traefik) that forwards to `http://<container-ip>:8080`.
- Behind a proxy, re-run the installer env or edit the service with `PLATEFUL_TRUST_PROXY=1` so rate limiting sees real client IPs. The proxy must send `X-Forwarded-Proto` and `X-Forwarded-For`.
- **Separate families.** Each family has its own private database file (`data/household_<n>.db`): their own people, plans, recipes, prices and groceries. Another family's admin can't see or touch yours. Your original data is family #1 and keeps `data/plateful.db`; accounts live in `data/accounts.db`.
- **How other families join:**
  - *Join an existing family:* a family admin creates an invite link (Settings → Invite family). The invitee joins that family as a member.
  - *Start their own family:* as the site owner (the first account), turn on **Settings → Site owner → "Let anyone create their own new family"**. A "Create your own family" link then appears on the sign-in page, and each signup gets a fresh private family and a setup wizard. Leave it off to keep the server invite-only. (A 100-family cap and per-IP limits apply; change the cap with `PLATEFUL_MAX_HOUSEHOLDS`.)
- The site owner can delete a family (and all its data) from the same Settings card.
- The recipe URL importer makes outbound requests from the server, so it needs internet access. The rest of the app works fully offline.

## Docker or local alternative

```bash
docker build -t plateful . && docker run -d -p 8080:8080 -v plateful-data:/data plateful
# or, anywhere with Python 3.8+:
python3 server.py        # PLATEFUL_PORT / PLATEFUL_HOST / PLATEFUL_DB env vars
```

## Notes and limits

- **Meijer data.** Meijer has no public price or stock API, so prices come from your Price Book. Live stock lookup would need a scraper, which is fragile and may break their terms, so it's left out for now.
- **Costs.** Costs are estimates. Items sold by the can, jar or head round up to whole units, and bulk-pack sizes aren't modeled.
- **Backup.** The data is one SQLite file at `/opt/plateful/data/plateful.db`. Use a Proxmox backup of the container, or Settings → JSON export.
- **No login.** There is no authentication, so keep it on your LAN or put it behind a reverse proxy.
