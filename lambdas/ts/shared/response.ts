/**
 * API Gateway HTTP API (payload format 2.0) response helpers.
 *
 * CORS headers are repeated on every response because the API-level
 * CorsConfiguration only handles preflight; actual responses still need them.
 */
import type { APIGatewayProxyResultV2 } from 'aws-lambda';

const CORS = {
  'content-type': 'application/json',
  'access-control-allow-origin': '*',
} as const;

function json(statusCode: number, body: unknown): APIGatewayProxyResultV2 {
  return { statusCode, headers: CORS, body: JSON.stringify(body) };
}

export const ok = (body: unknown) => json(200, body);
export const accepted = (body: unknown) => json(202, body);
export const badRequest = (error: string) => json(400, { error });
export const notFound = (error: string) => json(404, { error });

/** Logs the real error, returns a generic one. Never leak internals to callers. */
export function serverError(err: unknown): APIGatewayProxyResultV2 {
  console.error('unhandled error', err);
  return json(500, { error: 'internal error' });
}

/**
 * GitHub's own username rule: alphanumeric or single hyphens, cannot begin or
 * end with a hyphen, 1-39 characters. Worth enforcing at the edge so a bad
 * handle fails fast instead of burning a Lambda invocation and a GitHub call.
 */
const HANDLE_RE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;

export function validHandle(handle: string | undefined): handle is string {
  return typeof handle === 'string' && HANDLE_RE.test(handle);
}
