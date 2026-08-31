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

    // Reset to pending on every request so a re-run of a previously failed or
    // stale handle starts from a clean state rather than showing the old result.
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
