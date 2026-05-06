export default function HelpView() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-8 text-slate-200">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Plex NFO Builder — Help</h1>
        <p className="text-sm text-slate-400 mt-1">
          Generate Plex-compatible NFO files and artwork for your local library
          from TVDB, TMDB, and fanart.tv.
        </p>
      </header>

      <Section title="Versioning">
        <p>
          The version chip next to <b>Plex NFO</b> in the top bar shows the
          backend version that's actually running. When you run the{" "}
          <Code>:latest</Code> Docker tag, that chip is the source of truth
          for which numbered release Compose pulled — useful when a release
          rolls out and you want to confirm it landed.
        </p>
        <Bullets>
          <li>
            <b>Settings → About</b> shows the same version, the repository
            link, and the matching{" "}
            <Code>ghcr.io/cooper8386/plex-nfo-builder:vX.Y.Z</Code> container
            tag if you ever want to pin to a specific build.
          </li>
          <li>
            <Code>GET /api/version</Code> returns{" "}
            <Code>{"{version, name, repo}"}</Code> for scripts /
            health checks; <Code>GET /api/health</Code> includes the version
            too.
          </li>
        </Bullets>
      </Section>

      <Section title="Library sort order">
        <p>
          Library grids and lists order shows the way Plex / Sonarr / Radarr
          do, not by raw title. The sort key is decided per folder using a
          three-step fallback:
        </p>
        <Ol>
          <li>
            Your manual <Code>sorttitle</Code> override on the Overview →
            Overrides tab wins, full stop. Use this for the Yamato /
            Star Blazers split-anime case (one TVDB record, four
            community-recognised shows): set each folder's sorttitle to
            <Code>Star Blazers 2199 / 2202 / 2205 / 3199</Code> and they'll
            cluster together in the grid.
          </li>
          <li>
            Otherwise the provider's <Code>sortName</Code> field is used
            (TVDB and TMDB both publish one).
          </li>
          <li>
            If neither is set, the title is used with a leading{" "}
            <Code>The </Code> / <Code>A </Code> / <Code>An </Code>{" "}
            stripped — so <i>The Matrix</i> sorts under <b>M</b>, not <b>T</b>.
          </li>
        </Ol>
        <Callout>
          Editing a series or movie sorttitle override automatically refreshes
          the cached sort key. No rescan needed.
        </Callout>
      </Section>

      <Section title="Library status filter">
        <p>
          Each library toolbar has a three-way pill: <b>All</b> /{" "}
          <b>Needs work</b> / <b>Complete</b>. The choice is remembered
          per-library so each library opens to whichever filter you used
          last.
        </p>
        <Bullets>
          <li>
            <b>Needs work</b> surfaces folders whose status is{" "}
            <Badge>none</Badge>, <Badge>partial</Badge>, <Badge>stale</Badge>,{" "}
            <Badge>foreign</Badge>, or <Badge>mixed</Badge> — anything you'd
            want to open and finish.
          </li>
          <li>
            <b>Complete</b> shows only the green ones, so you can spot-check
            already-built shows.
          </li>
          <li>
            <b>All</b> is the unfiltered view.
          </li>
        </Bullets>
      </Section>

      <Section title="TMDB language filter (anime / foreign-language artwork)">
        <p>
          TMDB's image API treats the <Code>include_image_language</Code>
          parameter as a server-side filter — every uploaded poster is
          tagged with a language code, and posters whose code isn't in
          the request are dropped before the response leaves TMDB. So an
          anime whose every poster is flagged <Code>ja</Code>, or a
          K-drama whose posters are flagged <Code>ko</Code>, used to
          come back empty even though the artwork was published and
          public.
        </p>
        <Bullets>
          <li>
            <b>Auto-resolver</b> reads each title's TMDB <i>original
            language</i> and includes that flag alongside <Code>null</Code>
            and <Code>en</Code>. So a Japanese-original show now sees
            Japanese-tagged posters as candidates; a Korean-original
            show sees Korean-tagged posters; an English-original show
            still sees only language-less and English uploads, so a
            foreign poster won't get auto-picked for an English title.
            TMDB orders images by <Code>vote_average DESC</Code>, so
            the highest-rated upload still wins.
          </li>
          <li>
            <b>Manual artwork picker</b> requests <i>all</i> languages
            from TMDB — both for TMDB-bound titles and for TVDB-bound
            titles where TMDB supplementation is on. When you're
            hand-picking, the app shows you every uploaded poster,
            backdrop, and season poster TMDB has, no language filter
            applied.
          </li>
        </Bullets>
      </Section>

      <Section title="Pruning">
        <p>
          The library toolbar has two prune buttons for keeping the database
          honest with what's actually on disk:
        </p>
        <Bullets>
          <li>
            <b>Prune missing</b> — forgets folders that the database tracks
            but that no longer exist on disk. Useful after you delete a show
            via your file manager.
          </li>
          <li>
            <b>⚠ Prune empty</b> — forgets folders that exist on disk but
            contain <i>zero</i> media files. The classic case is a show
            you deleted whose folder still has <Code>tvshow.nfo</Code> +
            posters because the builder kept regenerating them. Both
            actions show a dry-run preview and require confirmation before
            touching anything.
          </li>
        </Bullets>
        <Callout>
          <b>Safety guarantees for Prune empty:</b> every candidate folder
          is re-walked on the live filesystem <i>immediately</i> before its
          row is deleted — a download that lands between the preview and
          your confirmation is detected and the folder is skipped. The
          action <b>never deletes any files on disk</b>; it only forgets
          the database row. Video, audio, and subtitle files cannot be
          touched by this path. If a folder gains media before deletion,
          the UI tells you how many were skipped.
        </Callout>
      </Section>

      <Section title="Scroll restoration">
        <p>
          Click into a show, then hit the back arrow (or your mouse-back
          button) and the library drops you back at the exact scroll
          position you came from — not the top of the page. The app
          remembers the last ten library views, so swapping between
          libraries also keeps each one's scroll where you left it.
        </p>
      </Section>

      <Section title="The 60-second tour">
        <Ol>
          <li>
            <b>Set your API keys.</b> In <Code>Settings</Code>, paste at least
            a TVDB API key. TMDB and fanart.tv are optional but improve artwork.
          </li>
          <li>
            <b>Add libraries.</b> Anything one level under <Code>/media</Code>{" "}
            (the path you mounted in <Code>docker-compose.yml</Code>) is auto-detected
            as a library. Use the sidebar's <Code>rescan</Code> link if you add
            new ones.
          </li>
          <li>
            <b>Match shows.</b> Open a library, then either click{" "}
            <Code>Auto-match all</Code> at the top, or open a show and search
            manually under the <Code>overview</Code> tab.
          </li>
          <li>
            <b>Build NFOs.</b> From the show's detail view, click{" "}
            <Code>Build NFOs</Code>. The app writes <Code>tvshow.nfo</Code>,{" "}
            <Code>season.nfo</Code>, per-episode <Code>.nfo</Code> files, and
            artwork (<Code>poster.jpg</Code>, <Code>background.jpg</Code>, season
            posters, etc.) directly into the show folder where Plex looks for
            them.
          </li>
          <li>
            <b>Point Plex at the same folder.</b> In Plex, the library should
            have the agent set to "Personal Media" (or any agent that reads
            local NFOs first) so Plex picks up the metadata you generated.
          </li>
        </Ol>
      </Section>

      <Section title="Build NFOs vs Force rebuild">
        <p>
          Both buttons end with the same set of files written to disk. The
          difference is what they do with the cache.
        </p>
        <Bullets>
          <li>
            <b>Build NFOs</b> uses cached responses from TVDB / TMDB whenever
            they exist. This is fast and is what you want 95% of the time. NFO
            files and artwork files on disk are always overwritten with the
            latest data, regardless of caching.
          </li>
          <li>
            <b>Force rebuild</b> bypasses the metadata cache and re-fetches
            everything from the upstream provider. Use it when:
            <Bullets>
              <li>
                a show was recently added/edited on TVDB or TMDB and you want
                the freshest copy,
              </li>
              <li>
                you switched the provider language and the cached response is
                still in the old language,
              </li>
              <li>
                you toggled an artwork setting and the cached image set is
                stale.
              </li>
            </Bullets>
          </li>
          <li>
            <b>Wipe NFOs &amp; artwork</b> deletes every NFO file (
            <Code>tvshow.nfo</Code>, <Code>season.nfo</Code>, episode{" "}
            <Code>.nfo</Code>) and every generated artwork file from the show
            folder — including per-episode{" "}
            <Code>&lt;stem&gt;-thumb.jpg</Code>/<Code>.png</Code> thumbnails next
            to videos (orphans from previous renames are wiped too). Season
            folders and your media files are never touched. Use this when you
            want to start fresh — the next <Code>Build NFOs</Code> click
            recreates everything.
          </li>
        </Bullets>
        <Callout>
          The on-disk <Code>.plex-nfo-builder.json</Code> sidecar is preserved
          by Wipe so your binding and overrides survive. It only gets removed
          if you call the API with <Code>keep_sidecar=false</Code> — or hit the
          <b> Blast every sidecar </b> button in the library Danger Zone.
        </Callout>
      </Section>

      <Section title="Library Danger Zone">
        <p>
          Each library page has a collapsible <b>Danger zone</b> panel
          (hazard-yellow border) pinned to the bottom of the page with two
          big buttons that operate
          across <i>every</i> folder tracked under the current library. Both
          run a dry-run preview first, show the exact file count, and require
          an explicit confirmation before touching disk.
        </p>
        <Bullets>
          <li>
            <b>Wipe ALL NFOs + artwork</b> — same operation as the per-show
            Wipe button, but applied to every folder in the library at once.
            Sidecars are preserved so bindings + overrides survive and you can
            rebuild straight after.
          </li>
          <li>
            <b>Blast every sidecar</b> — deletes every{" "}
            <Code>.plex-nfo-builder.json</Code> file in the library. The
            database is untouched, so the app keeps working; but the only
            on-disk record of bindings + overrides is gone, so a future
            database wipe would no longer restore them. NFOs and artwork are
            not touched. Use this when sidecars from a previous install have
            gone bad and you want to regenerate them on the next save.
          </li>
        </Bullets>
        <Callout>
          Both library-wide buttons are intentionally hazard-yellow rather than
          red — they're powerful but recoverable: the wipe is reversible by
          rebuilding, and the sidecar blast is reversible by re-saving any
          binding/override (which writes a fresh sidecar).
        </Callout>
      </Section>

      <Section title="NFO status badges">
        <Bullets>
          <li>
            <Badge>complete</Badge> the show has <Code>tvshow.nfo</Code> and
            an episode <Code>.nfo</Code> for every local episode, all written by
            this app.
          </li>
          <li>
            <Badge>partial</Badge> some episode NFOs are missing.
          </li>
          <li>
            <Badge>none</Badge> nothing has been built yet.
          </li>
          <li>
            <Badge>foreign</Badge> NFO files exist but were written by another
            tool (no app provenance tag).
          </li>
          <li>
            <Badge>mixed</Badge> the app's NFOs sit alongside extra unexpected
            ones — usually because a previous tool left files behind. Use{" "}
            <Code>Wipe NFOs &amp; artwork</Code> followed by{" "}
            <Code>Build NFOs</Code> to clean up.
          </li>
          <li>
            <Badge>stale</Badge> the metadata source has new content the local
            NFOs don't reflect. <Code>Force rebuild</Code> resolves this.
          </li>
        </Bullets>
      </Section>

      <Section title="Episode mapping & renaming">
        <p>
          Open a series and switch to the <Code>episodes</Code> tab. Each local
          file gets its own row, with the parsed season/episode on the left and
          the matched provider title on the right. Three filename styles are
          recognised automatically:
        </p>
        <Bullets>
          <li>
            Sonarr/Radarr: <Code>Series (Year) - S01E03 - Title.mkv</Code>
          </li>
          <li>
            Daily / talk shows: <Code>Show - 2024-01-15.mkv</Code>
          </li>
          <li>
            Anime / fansub: <Code>[Group] Title - 03 [1080p].mkv</Code> —
            treated as <Code>S01E03</Code> by default. If the fansub bundles
            multiple seasons, use the inline season picker on the row to pin
            each file to the right season.
          </li>
        </Bullets>
        <p>
          Override any file's mapping with the per-row dropdown — each
          override is keyed to the file path, so files that all parsed as
          <Code>S00E00</Code> no longer collide on a single row. Overrides
          are stored in SQLite and mirrored into the sidecar.
        </p>
        <p>
          Click <Code>Rename to scheme</Code> at the top of the Episodes tab to
          open the rename modal. It shows a dry-run preview of every{" "}
          <Code>from → to</Code> change, flags conflicts (<Code>exists</Code>,{" "}
          <Code>duplicate</Code>), and lets you uncheck individual files. Apply
          performs an atomic per-file rename and migrates the per-file override
          row alongside the file so your bindings survive.
        </p>
        <p>
          <b>Titles always come from your preferred language.</b> The series
          / movie title plugged into the rename template is re-fetched from
          the bound provider in the language set under{" "}
          <Code>Settings → Preferred language</Code> (with your fallback
          chain). Non-English originals like anime no longer leak the
          original-language title into renamed files — if you matched a show
          in English, it stays English on disk, even when the source's
          default name is Japanese / Korean / etc.
        </p>
        <p>
          <b>Companion files travel with the video.</b> When{" "}
          <Code>video.mkv</Code> becomes <Code>new-name.mkv</Code>, the
          matching <Code>video.nfo</Code>, <Code>video-thumb.jpg</Code>/
          <Code>.png</Code>, and known subtitle sidecars (<Code>.srt</Code>,{" "}
          <Code>.ass</Code>, <Code>.ssa</Code>, <Code>.vtt</Code>,{" "}
          <Code>.sub</Code>, <Code>.idx</Code>, <Code>.sup</Code> — with any
          language tag like <Code>video.en.forced.srt</Code>) are renamed in
          lockstep. No more orphan thumbnails or NFOs left behind under the old
          filename.
        </p>
        <p>
          The modal also has a <b>Series type</b> selector{" "}
          (<Code>Auto</Code> / <Code>Standard</Code> / <Code>Daily</Code> /{" "}
          <Code>Anime</Code>). <Code>Auto</Code> picks per file: anime fansub
          names get the anime template, files where the parser pulled an{" "}
          air-date get the daily template, everything else gets the standard
          template. Pin the selector when auto-detection guesses wrong.
        </p>
        <p>
          Templates use Sonarr/Radarr token grammar. Default schemes match the{" "}
          <a
            href="https://trash-guides.info/Sonarr/Sonarr-recommended-naming-scheme/"
            target="_blank"
            rel="noreferrer"
            className="text-indigo-400 hover:text-indigo-300 underline"
          >
            Trash Guides
          </a>{" "}
          recommendations and produce filenames like:
        </p>
        <Bullets>
          <li>
            Standard:{" "}
            <Code>Severance (2022) - S02E08 - Sweet Vitriol [WEBDL-1080p][HDR10][EAC3 Atmos 5.1][x265]-FLUX.mkv</Code>
          </li>
          <li>
            Daily:{" "}
            <Code>The Daily Show (1996) - 2024-05-08 - Episode Title [WEBDL-1080p]-NTb.mkv</Code>
          </li>
          <li>
            Anime:{" "}
            <Code>Frieren (2023) - S01E12 - The Land Where Souls Rest [WEBDL-1080p][10bit][x265][EAC3 5.1][EN+JA]-SubsPlease.mkv</Code>
          </li>
          <li>
            Movie:{" "}
            <Code>Blade Runner 2049 (2017) tmdb-335984 [Bluray-2160p][HDR10][TrueHD Atmos 7.1][x265]-FraMeSToR.mkv</Code>
          </li>
        </Bullets>
        <p>
          Codec, bit depth, HDR/DV detection, audio channels, and language
          tags are pulled directly from the file via <Code>ffprobe</Code> (the
          container ships <Code>ffmpeg</Code> from v0.11.0). The quality tag
          (<Code>WEBDL-1080p</Code>, <Code>Bluray-2160p</Code>, etc.) is
          synthesised from the source word in the original filename plus the
          probed resolution.
        </p>
        <p>
          Common tokens: <Code>{"{Series TitleYear}"}</Code>,{" "}
          <Code>{"{Episode CleanTitle}"}</Code>,{" "}
          <Code>{"{season:00}"}</Code>, <Code>{"{episode:00}"}</Code>,{" "}
          <Code>{"{Air-Date}"}</Code>, <Code>{"{Quality Full}"}</Code>,{" "}
          <Code>{"{MediaInfo VideoCodec}"}</Code>,{" "}
          <Code>{"{MediaInfo VideoBitDepth}"}</Code>,{" "}
          <Code>{"{MediaInfo VideoDynamicRangeType}"}</Code>,{" "}
          <Code>{"{MediaInfo AudioCodec}"}</Code>,{" "}
          <Code>{"{MediaInfo AudioChannels}"}</Code>,{" "}
          <Code>{"{MediaInfo AudioLanguages}"}</Code>,{" "}
          <Code>{"{Release Group}"}</Code>, <Code>{"{-Release Group}"}</Code>,{" "}
          <Code>{"{TvdbId}"}</Code>, <Code>{"{TmdbId}"}</Code>,{" "}
          <Code>{"{Movie CleanTitle}"}</Code>,{" "}
          <Code>{"{(Release Year)}"}</Code>. Conditional groups{" "}
          <Code>{"{[Token]}"}</Code> wrap rendered output in literal{" "}
          <Code>[..]</Code> brackets when the token resolves and drop the
          group entirely when it's empty.
        </p>
        <p>
          Old v0.10.0 simple tokens (<Code>{"{title}"}</Code>,{" "}
          <Code>{"{year}"}</Code>, <Code>{"{season:02}"}</Code>,{" "}
          <Code>{"{episode_title}"}</Code>, <Code>{"{quality}"}</Code>,{" "}
          <Code>{"{ext}"}</Code>) still work as fallbacks if you don't want
          the full Sonarr grammar.
        </p>
      </Section>

      <Section title="TVDB / TMDB external link">
        <p>
          Open any matched show or movie and look next to the title in the
          Detail view: a small <Code>TVDB ↗</Code> or <Code>TMDB ↗</Code>
          chip links straight to the public source page for whichever
          provider this folder is bound to. Opens in a new tab — handy when
          you want to double-check a match, copy a TVDB id, or just read the
          plot on the source site.
        </p>
      </Section>

      <Section title="Manual secondary TMDB / TVDB id">
        <p>
          Sometimes the primary provider's record doesn't list the other
          source's id. A TVDB show that has no TMDB cross-link, or a TMDB
          movie that has no TVDB id on file. The cross-provider artwork
          resolver and fanart.tv lookup both rely on that cross-id, so
          missing it means weaker artwork and an incomplete{" "}
          <Code>&lt;uniqueid&gt;</Code> block in the NFO.
        </p>
        <p>
          Open the show or movie, scroll to the{" "}
          <b>Secondary source</b> panel on the Overview tab, and either:
        </p>
        <Bullets>
          <li>
            <b>Paste</b> the id directly if you already know it (e.g. you
            looked the title up on themoviedb.org and grabbed the number
            from the URL).
          </li>
          <li>
            Or <b>search</b> the other provider in-place — same search box
            you use for the main matcher, but pre-pointed at the other
            source. Click <b>Link</b> on the right hit.
          </li>
        </Bullets>
        <p>
          Once linked you'll see a chip like <Code>tmdb-12345</Code> with an
          external-link button to the source page, plus <b>Edit</b> and{" "}
          <b>Clear</b>. The pinned id is used the next time you build NFOs
          for that folder: cross-provider artwork lookups prefer it,
          fanart.tv uses it, and the NFO emits a matching{" "}
          <Code>&lt;uniqueid type="tmdb"&gt;</Code> (or{" "}
          <Code>type="tvdb"</Code>) tag. The id is mirrored into
          <Code>.plex-nfo-builder.json</Code> so it survives a DB wipe.
        </p>
      </Section>

      <Section title="Per-show overrides">
        <p>
          Open any show or movie, switch to the <Code>overrides</Code> tab,
          and you can:
        </p>
        <Bullets>
          <li>
            Switch the metadata source to TVDB or TMDB just for that show, with
            a "Lock for this show" toggle so auto-match never silently swaps it.
          </li>
          <li>
            Edit the title, sort title, original title, tagline, and plot at
            the series, season, or per-episode level. Empty fields fall back to
            the source provider.
          </li>
          <li>
            Reset any field back to the source value with one click.
          </li>
        </Bullets>
        <p>
          Every override is written into both the database and a{" "}
          <Code>.plex-nfo-builder.json</Code> sidecar inside the folder. That
          means a database wipe is fully recoverable by re-detecting and
          rescanning — your bindings and overrides come straight back from
          disk.
        </p>
      </Section>

      <Section title="Mixing metadata and artwork sources">
        <p>
          Settings has two independent provider knobs:
        </p>
        <Bullets>
          <li>
            <b>Primary metadata source</b> drives auto-match, descriptions,
            cast, release dates, and every other text field in the NFO. You
            can also override this <b>per library</b> from the sidebar's
            ⋮ menu — handy when one TV library should pull from TVDB and
            another from TMDB. The override applies to auto-match, manual
            search, and the build pipeline; folders that already have a
            locked source binding are left alone.
          </li>
          <li>
            <b>Preferred artwork source</b> drives which provider's images
            (poster, background, banner, clearlogo, season posters) win
            during a build. Set it to <Code>tmdb</Code> while keeping
            metadata on <Code>tvdb</Code> if you prefer TMDB's poster
            library but TVDB's episode data — or the other way round.
            Per-show manual picks always override this, and if the
            preferred provider has no image for a slot the metadata
            source's own artwork fills in.
          </li>
        </Bullets>
      </Section>

      <Section title="Plex auto-refresh">
        <p>
          When a Plex base URL and token are set in Settings, the app can ask
          your Plex server to do a partial rescan of a show or movie folder
          right after a build finishes — so changes show up without you having
          to click refresh in Plex.
        </p>
        <p>
          Internally this is a <b>two-step dance</b>, because a plain Plex
          section scan only looks for <i>new media files</i> — it won't
          re-read an existing item's NFO or artwork on its own:
        </p>
        <Bullets>
          <li>
            Step 1: partial scan of the folder so any newly-added
            <code>.mkv</code>/<code>.mp4</code> files get picked up.
          </li>
          <li>
            Step 2: locate the matching show/movie by folder path and call
            Plex's per-item metadata refresh. This is what actually makes
            Plex re-read the updated <code>.nfo</code> and artwork.
          </li>
        </Bullets>
        <Bullets>
          <li>
            <b>Auto-refresh</b> fires after every successful Build NFOs / Force
            rebuild, with a small delay (default 5s) so writes settle to disk
            before Plex reads them.
          </li>
          <li>
            <b>Refresh in Plex</b> button on the detail page triggers an
            on-demand refresh for the current show or movie.
          </li>
          <li>
            <b>Path mappings</b> translate the app's view of disk to Plex's
            view. If the app sees <Code>/media/tv</Code> but Plex sees the same
            folder at <Code>/data/tv</Code>, add a mapping{" "}
            <Code>/media → /data</Code>. Longest matching prefix wins.
          </li>
          <li>
            If a brand-new folder hasn't been indexed by Plex yet, only
            the partial scan fires — there's no item to refresh until
            Plex finishes scanning. Re-run the refresh once the show
            appears in Plex and the NFO will be re-read.
          </li>
          <li>
            Refresh failures are logged but never fail the build itself — your
            NFOs and artwork are always written first.
          </li>
        </Bullets>
        <p>
          To get a Plex token, sign in to Plex Web, open any item, click the
          three-dot menu → Get Info → View XML, and copy the{" "}
          <Code>X-Plex-Token</Code> query parameter from the URL.
        </p>
      </Section>

      <Section title="Libraries: disable vs remove">
        <Bullets>
          <li>
            <b>Disable</b> hides the library and skips it on scans, but keeps
            every match, override, and item-state row in the database. Re-enable
            anytime from the sidebar's "Show disabled" toggle.
          </li>
          <li>
            <b>Remove from app…</b> forgets the library entirely — deletes its
            bindings, overrides, and item-state rows. Files on disk (NFOs,
            artwork, sidecars) are not touched, so a re-detect will bring it
            back populated from the sidecars on disk.
          </li>
        </Bullets>
      </Section>

      <Section title="Folder layout the app expects">
        <pre className="bg-slate-900 border border-slate-800 rounded p-3 text-xs leading-relaxed font-mono overflow-x-auto">
{`/media/
  tv/                                           ← library
    Severance (2022) {tvdb-371980}/             ← show folder
      Season 01/
        S01E01 - Episode Title.mkv              ← media file (untouched)
        S01E01 - Episode Title.nfo              ← built by this app
        season.nfo                              ← season-level NFO
      Season 02/...
      tvshow.nfo                                ← series NFO
      poster.jpg, background.jpg, ...           ← artwork
      .plex-nfo-builder.json                    ← sidecar (binding + overrides)
  movies/
    Alita Battle Angel (2019) {tmdb-399579}/
      Alita Battle Angel.mkv
      Alita Battle Angel.nfo
      poster.jpg, background.jpg`}
        </pre>
        <p>
          Folder names ending in <Code>{"{tvdb-12345}"}</Code> or{" "}
          <Code>{"{tmdb-67890}"}</Code> auto-match without a search.
        </p>
      </Section>

      <Section title="More info">
        <Bullets>
          <li>
            Source &amp; release notes:{" "}
            <a
              href="https://github.com/Cooper8386/plex-nfo-builder"
              target="_blank"
              rel="noreferrer"
              className="text-indigo-400 hover:text-indigo-300 underline"
            >
              github.com/Cooper8386/plex-nfo-builder
            </a>
          </li>
          <li>
            Logs are visible in <Code>Logs</Code>; long-running operations show
            up in <Code>Jobs</Code>.
          </li>
        </Bullets>
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: any }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold border-b border-slate-800 pb-1">
        {title}
      </h2>
      <div className="space-y-3 text-sm leading-relaxed text-slate-300">
        {children}
      </div>
    </section>
  );
}

function Ol({ children }: { children: any }) {
  return <ol className="list-decimal pl-6 space-y-2">{children}</ol>;
}

function Bullets({ children }: { children: any }) {
  return <ul className="list-disc pl-6 space-y-1.5">{children}</ul>;
}

function Code({ children }: { children: any }) {
  return (
    <code className="font-mono text-[12.5px] bg-slate-800/60 border border-slate-700 px-1 py-0.5 rounded">
      {children}
    </code>
  );
}

function Badge({ children }: { children: any }) {
  return (
    <span className="font-mono text-[11px] uppercase tracking-wider bg-slate-800 border border-slate-700 px-1.5 py-0.5 rounded mr-1">
      {children}
    </span>
  );
}

function Callout({ children }: { children: any }) {
  return (
    <div className="text-xs bg-indigo-900/20 border border-indigo-800/40 rounded px-3 py-2 text-slate-300">
      {children}
    </div>
  );
}
