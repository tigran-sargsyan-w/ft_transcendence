export class ApiError extends Error {
  readonly code: string; // 'EMAIL_ALREADY_EXISTS', 'AUTH_INVALID_CREDENTIALS', ...
  readonly status: number;

  constructor(code: string, message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

// <T> = "le type de data, que l'appelant précise"
export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers: { "Content-Type": "application/json", ...options.headers },
    });
  } catch {
    throw new ApiError("NETWORK_ERROR", "Cannot reach the backend", 0);
  }

  if (response.status === 204) return undefined as T; // logout : pas de corps

  const body = await response.json().catch(() => null);

  if (!response.ok) {
    throw new ApiError(
      body?.error?.code ?? "UNKNOWN",
      body?.error?.message ?? `Request failed (${response.status})`,
      response.status,
    );
  }
  return body.data as T;
}
