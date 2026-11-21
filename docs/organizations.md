# Organizations, locations and people

> **Status: built** (organizations, locations, events, people, pre-sync, event switcher, sharing). How to use it: [Using Vitrine](usage.md#organizations-locations-events-and-people). Whiteboard: [Vitrine: organizations, locations and roaming people](https://claude.ai/artifact/9Mn8wpuPA2zB49CMCoM5kZ).

## Why

Before, every agent received every active service, and a guest link belonged to one service. With several vMix PCs in several places, each one would have downloaded everyone's media, and a speaker who comes to two venues would have needed two links.

The server becomes the main hub: it keeps the database and the files and converts uploads. Each agent pulls only what its own location needs.

## The pieces

| Piece | What it is | Before |
|---|---|---|
| **Organization** | A team with its own staff accounts, library and people. Not separate customers: organizations can share media with each other. | (none: one implicit organization) |
| **Location** | One venue or hall. **A location is one agent, which is one vMix PC**, with its default input and transition settings. | the single agent |
| **Event** | A period at a location (one day, several days, open-ended or ongoing), with the sets that will be used there. | "service" (one date) |
| **Person** | Someone who brings media. Belongs to the organization and **roams**: one permanent link, one library, used at any event and any location. | "guest" (per service) |
| **Tablet** | A back or pulpit tablet. Stays with its location and talks to that agent over the LAN. Several back tablets per location are fine (e.g. two technicians). | same |

```
Server (main hub)
├── Organization A ──(share media, optional)── Organization B
│   ├── Zurich hall  = agent = vMix PC ── back ×2 + pulpit tablets
│   └── Geneva hall  = agent = vMix PC ── back + pulpit tablets
└── Organization B
    └── Bern hall    = agent = vMix PC ── back + pulpit tablets
```

## How a person roams

Staff decide where a person will be; the person doesn't need to know about locations.

1. Staff add Maria to an event: *Zurich hall, 22 Nov*. She gets (or already has) her permanent link.
2. Maria uploads or picks files with that link, days ahead if she likes.
3. The server converts them.
4. The Zurich agent sees Maria's set in its upcoming events and downloads the files **before the day**.
5. On the day the Zurich pulpit tablet shows her set, ready to tap.

Next week she speaks in Geneva: staff add her to the Geneva event. Her library, uploads and markers are already there; her Geneva set can start as a copy of her last set ("use my set from last time"). Her link shows an event switcher when she is in more than one upcoming event.

## What each agent receives

The manifest an agent downloads lists only:

- the events of **its location** that are active or upcoming (a configurable look-ahead, e.g. 14 days),
- the sets of those events and the files in them (including files shared by another organization),
- markers and revocations as today.

Files not used by any of its events are pruned as they are today.

## Sharing between organizations

- Each organization has its own library.
- An organization can **share with** chosen organizations: everything, or only files marked *shared*.
- Shared files appear in a **Shared** tab in the library of staff and people, labelled with the organization they come from.
- Only the server holds them; an agent downloads a shared file only when one of its events uses it.

## Tablets

Tablets don't roam: each is paired with its location's agent (QR as today) and keeps working on the LAN when the internet is down. A back tablet chooses among its location's events; the pulpit shows the set sent to it.

Ideas kept for later, if tablets ever need to move between venues: a server-signed tablet credential that every agent of the organization accepts offline, and a launcher page on the server domain that opens the right agent (matched by internet address, or picked from a list of locations). Tip for every venue: reserve a fixed LAN IP for the vMix PC in the router.

## Moving existing data

The migration creates one organization and one location ("Main") for the existing agent, turns each service into an event at that location and each guest into a person. Existing guest links keep working.

## Where it lives in the code

- Models: `Organization`, `Location`, `Person`, `PersonAlias`, `Service` (event), `Guest` (person in an event) in `server/guestmedia/models.py`; migration `0003_organizations`.
- What an agent receives: `events_for()` in `server/guestmedia/manifest.py`.
- Staff pages: `manage_views.py` (`people`, `person_detail`, `organization`, locations in `agents`); current organization in `orgs.py`.
- Guest page: `load_guest()` in `guest_views.py` picks the event from `?e=` or the person's next event.
