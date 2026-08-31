/**
 * GET /wrapped/{handle}/status
 *
 * Polled every couple of seconds while a run is in flight, so it stays a
 * single indexed primary-key lookup and nothing more.
 */
import type { APIGatewayProxyEventV2, APIGatewayProxyResultV2 } from 'aws-lambda';
import { sql } from '../shared/dataApi';
import { ok, badRequest, notFound, serverError, validHandle } from '../shared/response';
import type { JobStatus } from '../shared/types';

interface JobRow {
  handle: string;
  status: JobStatus;
  error: string | null;
  updated_at: string;
}

export const handler = async (
  event: APIGatewayProxyEventV2,
): Promise<APIGatewayProxyResultV2> => {
  try {
    const handle = event.pathParameters?.handle;
    if (!validHandle(handle)) return badRequest('invalid handle');

    const rows = await sql<JobRow>(
      `SELECT handle, status, error, updated_at
         FROM wrapped_jobs
        WHERE handle = :handle`,
      { handle },
    );

    if (rows.length === 0) return notFound('no run for handle');

    const job = rows[0];
    return ok({
      handle: job.handle,
      status: job.status,
      error: job.error,
      updatedAt: job.updated_at,
    });
  } catch (err) {
    return serverError(err);
  }
};
