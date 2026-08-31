/**
 * GET /wrapped/{handle}
 *
 * The payload the slide deck renders. Returns 404 until the run is `ready`,
 * so the front end can treat "not finished" and "never started" identically.
 */
import type { APIGatewayProxyEventV2, APIGatewayProxyResultV2 } from 'aws-lambda';
import { sql } from '../shared/dataApi';
import { ok, badRequest, notFound, serverError, validHandle } from '../shared/response';
import { SLIDE_TYPES, type WrappedSlide } from '../shared/types';

interface UserRow {
  handle: string;
  display_name: string | null;
  profile_image_url: string | null;
  bio: string | null;
  followers: number;
  public_repos: number;
  account_created_at: string | null;
}

interface SlideRow {
  slide_type: string;
  stats_json: string | null;
  html: string | null;
  generated_at: string | null;
}

export const handler = async (
  event: APIGatewayProxyEventV2,
): Promise<APIGatewayProxyResultV2> => {
  try {
    const handle = event.pathParameters?.handle;
    if (!validHandle(handle)) return badRequest('invalid handle');

    const status = await sql<{ status: string }>(
      'SELECT status FROM wrapped_jobs WHERE handle = :handle',
      { handle },
    );
    if (status.length === 0 || status[0].status !== 'ready') {
      return notFound('not ready');
    }

    const [users, slides] = await Promise.all([
      sql<UserRow>(
        `SELECT handle, display_name, profile_image_url, bio, followers,
                public_repos, account_created_at
           FROM users
          WHERE handle = :handle`,
        { handle },
      ),
      sql<SlideRow>(
        `SELECT slide_type, stats_json, html, generated_at
           FROM slides
          WHERE handle = :handle`,
        { handle },
      ),
    ]);

    if (users.length === 0) return notFound('not ready');
    const u = users[0];

    const bySlideType = new Map(slides.map((s) => [s.slide_type, s]));

    // Ordered by slide-types.json, not by whatever the database returned, so
    // the deck always plays in the intended sequence. A slide that failed to
    // generate still appears, with html: null -- the front end decides whether
    // to skip it or show a fallback.
    const ordered: WrappedSlide[] = SLIDE_TYPES.map((def) => {
      const row = bySlideType.get(def.id);
      return {
        slideType: def.id,
        title: def.title,
        stats: row?.stats_json ? safeParse(row.stats_json) : null,
        html: row?.html ?? null,
        generatedAt: row?.generated_at ?? null,
      };
    });

    return ok({
      user: {
        handle: u.handle,
        displayName: u.display_name,
        profileImageUrl: u.profile_image_url,
        bio: u.bio,
        followers: u.followers,
        publicRepos: u.public_repos,
        accountCreatedAt: u.account_created_at,
      },
      slides: ordered,
    });
  } catch (err) {
    return serverError(err);
  }
};

/**
 * MySQL JSON columns come back as strings through the Data API. A malformed
 * one shouldn't take down the whole response -- that slide just loses stats.
 */
function safeParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    console.warn('unparseable stats_json', value.slice(0, 200));
    return null;
  }
}
