/**
 * POST /wrapped/{handle}
 *
 * Marks a run as pending and kicks off the pipeline asynchronously, then
 * returns immediately. The client polls /status from here.
 */
import type { APIGatewayProxyEventV2, APIGatewayProxyResultV2 } from 'aws-lambda';
import { LambdaClient, InvokeCommand } from '@aws-sdk/client-lambda';
import { sql } from '../shared/dataApi';
import { accepted, badRequest, serverError, validHandle } from '../shared/response';

const lambda = new LambdaClient({});
const NEXT_FN = process.env.NEXT_FN!;

export const handler = async (
  event: APIGatewayProxyEventV2,
): Promise<APIGatewayProxyResultV2> => {
  try {
    const handle = event.pathParameters?.handle;
    if (!validHandle(handle)) return badRequest('invalid handle');

    // A full run re-fetches from GitHub and makes five sequential-ish Bedrock
    // calls -- real latency even at best. If this handle already finished,
    // skip straight back to the front end instead of redoing all of that, so
    // re-showing a handle (a repeat demo, a double-click) is instant rather
    // than a full re-run. ?refresh=true bypasses this and forces a clean run.
    const forceRefresh = event.queryStringParameters?.refresh === 'true';
    if (!forceRefresh) {
      const existing = await sql<{ status: string }>(
        `SELECT status FROM wrapped_jobs WHERE handle = :handle`,
        { handle },
      );
      if (existing[0]?.status === 'ready') {
        return accepted({ handle, status: 'ready' });
      }
    }

    // Reset to pending on every other request so a re-run of a previously
    // failed or stale handle starts from a clean state rather than showing
    // the old result.
    await sql(
      `INSERT INTO wrapped_jobs (handle, status, error)
       VALUES (:handle, 'pending', NULL)
       ON DUPLICATE KEY UPDATE status = 'pending', error = NULL`,
      { handle },
    );

    // Event = fire and forget. The pipeline owns its own error reporting from
    // here on, by writing status back to wrapped_jobs.
    await lambda.send(
      new InvokeCommand({
        FunctionName: NEXT_FN,
        InvocationType: 'Event',
        Payload: Buffer.from(JSON.stringify({ handle })),
      }),
    );

    return accepted({ handle, status: 'pending' });
  } catch (err) {
    return serverError(err);
  }
};
