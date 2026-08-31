/**
 * Thin wrapper over the RDS Data API.
 *
 * The raw API returns rows as arrays of tagged unions -- [{stringValue: "x"},
 * {longValue: 3}] -- which is miserable to work with. Passing
 * formatRecordsAs: JSON makes Aurora serialise rows server-side into a JSON
 * string of plain objects instead, so callers get ordinary JS values.
 *
 * Use sql() rather than reaching for RDSDataClient directly, so that the
 * parameter conversion and the JSON unwrapping stay in one place.
 */
import {
  RDSDataClient,
  ExecuteStatementCommand,
  type SqlParameter,
  type Field,
} from '@aws-sdk/client-rds-data';

const client = new RDSDataClient({});

const RESOURCE_ARN = process.env.DB_CLUSTER_ARN!;
const SECRET_ARN = process.env.DB_SECRET_ARN!;
const DATABASE = process.env.DB_NAME ?? 'gh_wrapped';

/** Values a caller may bind. null is allowed; undefined is not (it's a bug). */
export type Bindable = string | number | boolean | null;

function toField(value: Bindable): Field {
  if (value === null) return { isNull: true };
  switch (typeof value) {
    case 'string':
      return { stringValue: value };
    case 'boolean':
      return { booleanValue: value };
    case 'number':
      return Number.isInteger(value)
        ? { longValue: value }
        : { doubleValue: value };
    default:
      throw new Error(`cannot bind ${typeof value} to a SQL parameter`);
  }
}

/**
 * Run a statement with named parameters.
 *
 *   await sql('SELECT * FROM users WHERE handle = :handle', { handle: 'octocat' })
 *
 * Always use named parameters. Interpolating into the string is both an
 * injection risk and slower (no server-side plan reuse).
 */
export async function sql<T = Record<string, unknown>>(
  statement: string,
  params: Record<string, Bindable> = {},
): Promise<T[]> {
  const parameters: SqlParameter[] = Object.entries(params).map(
    ([name, value]) => ({ name, value: toField(value) }),
  );

  const res = await client.send(
    new ExecuteStatementCommand({
      resourceArn: RESOURCE_ARN,
      secretArn: SECRET_ARN,
      database: DATABASE,
      sql: statement,
      parameters,
      formatRecordsAs: 'JSON',
    }),
  );

  // formattedRecords is absent for statements that return no rows (INSERT,
  // UPDATE, DDL) -- that is not an error, it's just an empty result.
  if (!res.formattedRecords) return [];
  return JSON.parse(res.formattedRecords) as T[];
}
