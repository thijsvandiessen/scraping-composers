import { z } from "zod";

// The consumer API publishes no types, so these zod schemas mirror its Pydantic
// response models (apps/consumer-api/src/composer_api/schemas.py) and validate
// every response at the boundary. Loose objects: fields the API adds later must
// not break the frontend.

export const ComposerSummarySchema = z.looseObject({
  id: z.uuid(),
  label: z.string(),
  created_at: z.string(),
  concert_count: z.number().int(),
});

export const ComposerPageSchema = z.looseObject({
  items: z.array(ComposerSummarySchema),
  total: z.number().int(),
  page: z.number().int(),
  limit: z.number().int(),
});

export const ClaimSchema = z.looseObject({
  predicate: z.string(),
  value: z.string().nullable(),
  object_label: z.string().nullable(),
  object_id: z.uuid().nullable(),
  source: z.string(),
  // The exact source page the claim came from (e.g. https://www.wikidata.org/wiki/Q255),
  // or the source homepage when no page URL is known.
  source_url: z.string().nullable(),
  // The source's own id for this composer (e.g. the wikidata QID "Q255").
  // Optional: tolerates consumer APIs predating the field.
  source_external_id: z.string().nullable().optional(),
});

export const ComposerDetailSchema = z.looseObject({
  id: z.uuid(),
  label: z.string(),
  kind: z.string(),
  created_at: z.string(),
  claims: z.array(ClaimSchema),
});

export type ComposerSummary = z.infer<typeof ComposerSummarySchema>;
export type ComposerPage = z.infer<typeof ComposerPageSchema>;
export type Claim = z.infer<typeof ClaimSchema>;
export type ComposerDetail = z.infer<typeof ComposerDetailSchema>;
