import React from "react";

export type ApiOptions = RequestInit & { recruiterToken?: string };

export async function api<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { recruiterToken, ...request } = options;
  const headers = new Headers(request.headers);
  if (recruiterToken) headers.set("Authorization", `Bearer ${recruiterToken}`);
  if (request.body && !(request.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...request, headers });
  let body: unknown = null;
  try { body = await response.json(); } catch { body = null; }
  if (!response.ok) {
    const detail = typeof body === "object" && body && "detail" in body ? String(body.detail) : response.statusText;
    throw new Error(detail || `HTTP ${response.status}`);
  }
  return body as T;
}

export function useRecruiterToken(): [string, (value: string) => void] {
  const [token, setState] = React.useState(() => localStorage.getItem("recruiter-token") || "");
  const setToken = (value: string) => {
    setState(value);
    localStorage.setItem("recruiter-token", value);
  };
  return [token, setToken];
}
