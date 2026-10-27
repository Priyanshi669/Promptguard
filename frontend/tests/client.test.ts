/**
 * Unit tests for the typed API client: envelope decoding, transport
 * fallback, and token attachment — all against a stubbed fetch.
 */

import { afterEach, describe, expect, it, vi } from "vitest";
import {
  ApiError,
  TransportError,
  cancelJob,
  createRun,
  getRun,
  setApiTokenProvider,
} from "../src/api/client";

const OK = { status: 200, ok: true, json: async () => ({}) };
const envelope = (status: number, body: object) => ({
  status,
  ok: status >= 200 && status < 300,
  text: async () => JSON.stringify(body),
});

afterEach(() => {
  vi.unstubAllGlobals();
  setApiTokenProvider(() => null);
});

describe("typed api client", () => {
  it("decodes the canonical error envelope into ApiError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        envelope(404, {
          error: {
            code: "run_not_found",
            message: "Run abc not found.",
            request_id: "req-1",
          },
        }),
      ),
    );

    const err = await getRun("abc").then(
      () => null,
      (caught: unknown) => caught,
    );
    expect(err).toBeInstanceOf(ApiError);
    const apiError = err as ApiError;
    expect(apiError.status).toBe(404);
    expect(apiError.code).toBe("run_not_found");
    expect(apiError.message).toBe("Run abc not found.");
    expect(apiError.requestId).toBe("req-1");
  });

  it("falls back to TransportError when the body is not the envelope", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => envelope(502, { something: "else" })),
    );

    const err = await getRun("abc").then(
      () => null,
      (caught: unknown) => caught,
    );
    expect(err).toBeInstanceOf(TransportError);
    expect((err as TransportError).status).toBe(502);
  });

  it("attaches the bearer token only when a provider is configured", async () => {
    const fetchMock = vi.fn(async () => ({
      ...OK,
      json: async () => ({ job_id: "j", status: "cancelled", cancelled: true }),
    }));
    vi.stubGlobal("fetch", fetchMock);
    setApiTokenProvider(() => "secret-token");

    await cancelJob("j");
    const init = ((fetchMock.mock.calls[0] as unknown[])[1] ??
      {}) as RequestInit;
    const headers = new Headers(init.headers);
    expect(headers.get("Authorization")).toBe("Bearer secret-token");
    // Bodyless requests stay free of a spurious Content-Type.
    expect(headers.get("Content-Type")).toBeNull();
  });

  it("posts the full run request as JSON", async () => {
    const fetchMock = vi.fn(async () => ({
      ...OK,
      json: async () => ({
        job_id: "job-1",
        status: "queued",
        created_at: "now",
        duplicate: false,
      }),
    }));
    vi.stubGlobal("fetch", fetchMock);

    const created = await createRun({ prompt_version: "v1", limit: 5 });
    expect(created.job_id).toBe("job-1");
    const init = ((fetchMock.mock.calls[0] as unknown[])[1] ??
      {}) as RequestInit;
    expect(init.method).toBe("POST");
    expect(String(init.body)).toBe(
      JSON.stringify({ prompt_version: "v1", limit: 5 }),
    );
  });
});
