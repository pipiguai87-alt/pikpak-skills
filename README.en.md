# pikpak-skills

**14 AI agent skills that keep your PikPak cloud drive in order.**

Wipe out bundled ad files, dedupe multi-version releases, save links remotely, run storage checkups, index your media library — turn a messy pile of downloads into a tidy, searchable collection.

[![skills.sh](https://skills.sh/b/pipiguai87-alt/pikpak-skills)](https://skills.sh/pipiguai87-alt/pikpak-skills)

| Skill | What it does |
|-------|--------------|
| **pikpak-onboarding** | Zero-to-hero PikPak setup in 5 minutes: install → register → first magnet → stream online |
| **pikpak-ad-cleaner** | Scan torrent packs for bundled junk (.url shortcuts, promo videos, QR images), get a tiered report, clean with confirmation |
| **pikpak-dedup** | Find duplicate releases (1080p / 4K / REMUX of the same title), keep the best, trash the rest |
| **pikpak-save** | One-shot cloud download for magnets / links / share links, with progress tracking |
| **pikpak-storage-report** | Storage checkup: folder size ranking, largest files, reclaimable space |
| **pikpak-telegram-bot** | Control PikPak from Telegram: drop a link in chat, it lands in your drive |
| **pikpak-media-index** | Build a searchable media index: duration, resolution, codec, source |
| **pikpak-share-manager** | Share management: bulk create, expiry audit, view stats |
| **pikpak-batch-backup** | Bulk download your drive back to local disk, resumable with manifest |
| **pikpak-task-guardian** | Watch download tasks: retry failures, ping you when done |
| **pikpak-search** | Cloud search: walk the whole drive by keyword/regex (fills the official search gap) |
| **pikpak-magnet-pipeline** | Magnet pipeline: magnet → cloud → local → cleanup, resumable stepwise |
| **pikpak-webdav-guide** | WebDAV guide: enable → mount in Infuse/VidHub/Emby/Alist/rclone; bypass ISP throttling |
| **pikpak-speed-guide** | Speed guide: diagnose → WebDAV bypass → local acceleration proxy (10-30MB/s) |

## Where to start?

**New to PikPak** → `pikpak-onboarding`
**Drive full of junk/dupes** → `pikpak-ad-cleaner` + `pikpak-dedup` (scan first, review, then clean)
**Building a resource pipeline** → `pikpak-save` + `pikpak-telegram-bot`

### Install

Tell your AI agent (Claude Code / Codex / Cursor / Hermes — 60+ agents supported):

```
npx skills add pipiguai87-alt/pikpak-skills --skill pikpak-onboarding
```

Or install everything:

```
npx skills add pipiguai87-alt/pikpak-skills
```

Then just talk to your agent in plain language: "scan my PikPak for ad files", "find duplicate movies in my drive" — the skill takes over.

> Prerequisite: PikPak CLI (official install page: https://mypikpak.com/cli — review and install it yourself).

## Design principles

- **Read-only first**: cleanup and dedup tools only run `pikpak ls` scans (enforced at source level, with regression tests watching), never silently delete
- **User confirmation**: every cleanup goes through your explicit review of the item list
- **Recoverable**: deletions go to trash via `pikpak rm` (restorable), never permanent destroy
- **Content-neutral**: tools work on filenames/metadata only

## About the referral code

`pikpak-onboarding` ships with a referral code in `config.json`. Per PikPak's official rules, the skill **discloses the code and its owner before registration**, and you may decline it (`pikpak auth register --affiliate=`). No registration ever happens without your knowledge.

## Development

```bash
python3 dev/run_tests.py    # 25 offline regression tests (neutral fixtures, no network)
bash dev/smoke_e2e.sh       # end-to-end smoke with a mock CLI (no real account)
```

Coverage: ad tiering rules, pagination walk, semantic-token-preserving dedup normalization, share expiry audit, path traversal sanitization, and AST-level "read-only" safety assertions.

## Boundaries

- Only for cloud content you are **entitled to manage**
- Does not help obtain, organize, or distribute piracy or illegal content
- Refuses service for illegal material (e.g. non-consensual recordings)

## License

MIT
