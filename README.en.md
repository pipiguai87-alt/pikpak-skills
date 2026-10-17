# pikpak-skills

**14 AI agent skills that keep your PikPak cloud drive in order.**

Review bundled-ad candidates and duplicate releases, save links, inspect storage, and compare bounded download samples. Cleanup requires confirmation. Third-party tools and performance gains require separate verification; no fixed speedup is promised.

[![skills.sh](https://skills.sh/b/pipiguai87-alt/pikpak-skills)](https://skills.sh/pipiguai87-alt/pikpak-skills)

| Skill | What it does | Current maturity (not production-recommended) |
|-------|--------------|-----------------------------------------------|
| **pikpak-onboarding** | Guided setup and registration, with upfront referral disclosure and an explicit opt-out | Experimental: operation cards/offline planner; live registration unverified |
| **pikpak-ad-cleaner** | Tiered ad-candidate scans; print-only commands require a frozen report and explicit IDs | Experimental: offline scan/approval preview; live account unverified |
| **pikpak-dedup** | Media-family-isolated release candidates; similar names never prove identical content | Experimental: offline candidates; no proof of identical content |
| **pikpak-save** | Save/share operation cards and an offline exact-task-ID inspector; not a background executor | Experimental: offline inspection; live save acceptance incomplete |
| **pikpak-storage-report** | Folder and large-file rankings with unknown sizes; enumeration is not account quota | Experimental: offline enumeration; real quota unverified |
| **pikpak-telegram-bot** | Separate chat/sender authorization and durable dedup; uncertain submissions are not replayed | Experimental: offline authorization/dedup/backoff; live integration unverified |
| **pikpak-media-index** | Video metadata/source index, spreadsheet-safe CSV and redacted URL parameters | Experimental: offline export; real CLI schema unverified |
| **pikpak-share-manager** | Read-only paginated expiry/activity audit; missing fields remain unknown | Experimental: offline audit; real share listing unverified |
| **pikpak-batch-backup** | One-pass downloads and local manifest; rejects collisions, overwrites and short files; no resume | Experimental: offline download safety; real backup unverified |
| **pikpak-task-guardian** | Observe by default; opt-in notifications and exact-source resubmission with durable backoff/budget | Experimental: offline guardian policy; live daemon unverified |
| **pikpak-search** | Keyword/regex file or folder traversal; absent CLI search does not imply a removed service API | Experimental: offline traversal; real CLI schema unverified |
| **pikpak-magnet-pipeline** | Stepwise single-file state, receipt-bound cleanup preview; never deletes automatically | Experimental: offline stages; live end-to-end path unverified |
| **pikpak-webdav-guide** | Official WebDAV setup, dedicated credentials and quota limits; no default account query | Experimental: operation cards/offline metadata; real mount unverified |
| **pikpak-speed-guide** | Account-free reachability and integrity-gated A/B sample measurements; no QoS diagnosis | Experimental: loopback HTTP samples; real throughput unverified |

Listing a skill does not recommend it as usable in production. Promotion requires both a legal, neutral-data real-service acceptance path for that use case and the relevant independent safety review. Fixtures, fake CLIs, test counts and directory listings cannot substitute for either.

## Where to start?

**New to PikPak** → `pikpak-onboarding`
**Drive full of junk/dupes** → `pikpak-ad-cleaner` + `pikpak-dedup` (scan first, review, then clean)
**Building a resource pipeline** → `pikpak-save` + `pikpak-telegram-bot`
**Slow downloads or seeking** → `pikpak-speed-guide` (measure before choosing a path)
**Standard WebDAV access** → `pikpak-webdav-guide` (membership and quota limits apply)

### Install

Install in a tool supporting Agent Skills, such as Claude Code / Codex / Cursor / Hermes:

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
- **No automatic deletion**: only approved command previews; recovery and released capacity need independent verification
- **Content-neutral**: tools work on filenames/metadata only

## Capabilities and verification limits

- This is an offline-validated edition. Fixtures, fake CLIs and loopback HTTP do not establish real-account compatibility, actual vendor JSON fields or speed gains.
- Every skill's `references/upstream-design.md` records pinned GitHub sources, licenses, adoption/rejection reasons and outstanding validation. No upstream code is bundled or executed.
- rclone is an optional external client; native PikPak and standard WebDAV are different backends. Experimental acceleration proxies are not promoted to production engines on author benchmarks alone.
- No unimplemented resume, incremental backup, cross-name content dedup or scheduled daily reports are promised. Some save/share operations remain human-reviewed operation cards.
- Live accounts, messages, downloads, notifications, resubmissions and cleanup each require the corresponding explicit authorization; installing a skill grants none.

## About the referral code

Every `skills/<name>/SKILL.md` directly carries the suite's referral disclosure: the public code is `22874035`; it may reward the sharer, and benefits follow PikPak's official page. You may decline without affecting that skill. Only `pikpak-onboarding` owns the pre-registration `registration_plan.py` plan entrypoint; the other 13 skills do not auto-register or log in and link back to onboarding.

After informed consent, the user may execute: `pikpak auth register --affiliate=22874035`. Declining or leaving the code blank requires the explicit command `pikpak auth register --affiliate=`. Omitting the flag or merely clearing an environment variable may inherit the installer's recorded referral and is not a reliable opt-out. Per-skill evidence and regressions are kept in a private audit record outside this repository; real registration, account compatibility and public registry refresh remain separate acceptance steps.

## Development

```bash
python3 dev/run_tests.py    # neutral fixtures + loopback HTTP; no live account or internet
bash dev/smoke_e2e.sh       # end-to-end smoke with a mock CLI (no real account)
```

Coverage includes per-skill regressions for approvals, pagination/cycles, typed candidates, unknown metadata, path/download integrity, task dedup/backoff, sender authorization, notifications and sample measurements. The unified runner isolates HOME/PATH/PIKPAK_BIN, rejects child processes by default, and propagates Python audit restrictions into explicitly approved fake CLI and sampling-worker children via an isolated bootstrap. Covered Python socket, resolver, subprocess and notification paths are blocked except for approved neutral loopback traffic. This is defense in depth, not an OS sandbox or a guarantee against malicious native-code escapes. Smoke checks force a private mock CLI instead of inheriting a live CLI override. The optional rendered-Markdown check needs an existing Markdown parser; without one it is explicitly skipped, not passed. None of these checks establish real-account compatibility or speed gains.

## Boundaries

- Only for cloud content you are **entitled to manage**
- Does not help obtain, organize, or distribute piracy or illegal content
- Refuses service for illegal material (e.g. non-consensual recordings)

## License

MIT
