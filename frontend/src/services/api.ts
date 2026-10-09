export interface ApiSatellite {
  id: number;
  name: string | null;
  shell: number | null;
  orbit: number | null;
  sublat: number;
  sublong: number;
  altitude: number;
}

export interface ApiISLEdge {
  src_sat_id: number;
  dst_sat_id: number;
  src_lat: number;
  src_lon: number;
  dst_lat: number;
  dst_lon: number;
  src_alt: number;
  dst_alt: number;
  cross_shell: boolean;
  degraded: boolean;
}

export interface ApiRoutingPath {
  src_lat: number;
  src_lon: number;
  dst_lat: number;
  dst_lon: number;
  src_sat_id: number | null;
  dst_sat_id: number | null;
  routing_degradation_aware: boolean;
  task_state: string | null;
  e2e_delay_s: number | null;
  e2e_bandwidth_mbps: number | null;
  e2e_packet_loss: number | null;
  e2e_success_prob: number | null;
  dst_gs: string | number | null;
  path: number[];
}

export interface ApiGroundStation {
  id: number | string | null;
  gs_name: string | null;
  lat: number;
  lon: number;
}

export interface ApiFrame {
  iteration: number;
  sim_time_utc: string | null;
  satellites: ApiSatellite[];
  isl_edges: ApiISLEdge[];
  routing_paths: ApiRoutingPath[];
  gs_stations: ApiGroundStation[];
}

export interface ApiIterationMeta {
  iteration: number;
  sim_time_utc: string | null;
  satellite_count: number;
  isl_edge_count: number;
  routing_path_count: number;
  gs_count: number;
}

interface MetadataResponse {
  dataset: string;
  iterations: ApiIterationMeta[];
}

export interface DatasetOption {
  name: string;
}

const trimBase = (value: string): string => (value || "").trim().replace(/\/$/, "");
const API_BASE = trimBase(import.meta.env.VITE_API_BASE_URL || "/api");
const API_ROOT = (() => {
  const base = API_BASE === "/" ? "/api" : API_BASE;
  if (base === "") {
    return "/api";
  }
  const normalizedPath = base.replace(/^https?:\/\/[^/]+/i, "");
  if (normalizedPath === "/api" || /(^|\/)api$/.test(normalizedPath)) {
    return base;
  }
  return `${base}/api`;
})();

const apiUrl = (path: string): string => {
  const normalizedPath = path.startsWith("/") ? path : `/${path}`;
  return `${API_ROOT}${normalizedPath}`;
};

async function parseJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API error ${res.status}: ${body.slice(0, 200)}`);
  }
  const text = await res.text();
  if (!text || text.trim().startsWith("<")) {
    throw new Error(`API did not return JSON (received HTML/invalid response): ${text.slice(0, 200)}`);
  }
  try {
    return JSON.parse(text) as T;
  } catch (err) {
    throw new Error(`Invalid JSON response from API: ${(err as Error).message}`);
  }
}

export async function fetchMetadata(dataset?: string): Promise<MetadataResponse> {
  const query = dataset ? `?dataset=${encodeURIComponent(dataset)}` : "";
  const res = await fetch(apiUrl(`/metadata${query}`));
  return parseJson<MetadataResponse>(res);
}

export async function fetchFrames(start?: number, end?: number, dataset?: string): Promise<ApiFrame[]> {
  const q: string[] = [];
  if (start !== undefined) q.push(`start=${encodeURIComponent(String(start))}`);
  if (end !== undefined) q.push(`end=${encodeURIComponent(String(end))}`);
  if (dataset !== undefined) q.push(`dataset=${encodeURIComponent(dataset)}`);
  const query = q.length ? `?${q.join("&")}` : "";
  const res = await fetch(apiUrl(`/frames${query}`));
  return parseJson<ApiFrame[]>(res);
}

export async function fetchDatasets(): Promise<DatasetOption[]> {
  const res = await fetch(apiUrl("/datasets"));
  return parseJson<DatasetOption[]>(res);
}
