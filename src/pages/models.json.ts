import type { APIRoute } from 'astro';
import { getModelList } from '../lib/models';
import { STATUS_ORIGIN } from '../lib/status';

export const GET: APIRoute = async () => {
  const models = await getModelList('en');
  // Resolve relative status URLs to absolute so client-side fetch from any
  // page (including /browse where many cards live) works without guessing.
  const items = models.map((m) => ({
    ...m,
    _status_url: m._status_url ? `${STATUS_ORIGIN}${m._status_url}` : undefined,
  }));
  return new Response(JSON.stringify(items, null, 2), {
    headers: {
      'Content-Type': 'application/json',
      'Cache-Control': 'public, max-age=3600',
    },
  });
};
