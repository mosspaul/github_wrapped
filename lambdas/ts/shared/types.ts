/**
 * Shapes shared between the API handlers and the front end.
 * The authoritative description of these lives in shared/CONTRACTS.md.
 */
import slideTypesJson from '../../../shared/slide-types.json';

export interface SlideTypeDef {
  id: string;
  title: string;
  blurb: string;
}

/** Single source of truth for slide ids and their display order. */
export const SLIDE_TYPES: SlideTypeDef[] = slideTypesJson.slideTypes;

export const SLIDE_ORDER: string[] = SLIDE_TYPES.map((s) => s.id);

export type JobStatus =
  | 'pending'
  | 'ingesting'
  | 'computing'
  | 'generating'
  | 'ready'
  | 'error';

export interface WrappedUser {
  handle: string;
  displayName: string | null;
  profileImageUrl: string | null;
  bio: string | null;
  followers: number;
  publicRepos: number;
  accountCreatedAt: string | null;
}

export interface WrappedSlide {
  slideType: string;
  title: string;
  stats: unknown;
  html: string | null;
  generatedAt: string | null;
}

export interface WrappedPayload {
  user: WrappedUser;
  slides: WrappedSlide[];
}
