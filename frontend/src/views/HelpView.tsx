export default function HelpView() {
  return (
    <div className="max-w-3xl mx-auto px-6 py-8 space-y-8 text-slate-200">
      <header>
        <h1 className="text-2xl font-bold tracking-tight">Plex NFO Builder — Help</h1>
        <p className="text-sm text-slate-400 mt-1">
          Generate Plex-compatible NFO files and artwork for your local library
          from TheTVDB, TMDB, and fanart.tv.
        </p>
      </header>

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
            folder. Season folders and your media files are never touched. Use
            this when you want to start fresh — the next <Code>Build NFOs</Code>
            click recreates everything.
          </li>
        </Bullets>
        <Callout>
          The on-disk <Code>.plex-nfo-builder.json</Code> sidecar is preserved
          by Wipe so your binding and overrides survive. It only gets removed
          if you call the API with <Code>keep_sidecar=false</Code>.
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
            cast, release dates, and every other text field in the NFO.
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
