import { useCallback, useEffect, useRef, useState } from "react";

import { api, ApiError, type ModelView } from "../api";

export interface AnalysisState<T> {
  jobId: string | null;
  status: string | null;
  result: T | null;
  error: string | null;
  busy: boolean;
}

/** Requests an analysis job (replay or strategy table) and polls until it finishes. */
export function useAnalysis<T>(command: "request_replay" | "request_strategy") {
  const [state, setState] = useState<AnalysisState<T>>({
    jobId: null,
    status: null,
    result: null,
    error: null,
    busy: false,
  });
  const timer = useRef<number | null>(null);

  const stop = () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  };
  useEffect(() => stop, []);

  const poll = useCallback((jobId: string) => {
    void api
      .query<{ status: string; result: T | null; error: string }>("analysis", jobId)
      .then((answer) => {
        const finished = answer.status === "succeeded" || answer.status === "failed" || answer.status === "cancelled";
        setState((current) =>
          current.jobId !== jobId
            ? current
            : {
                ...current,
                status: answer.status,
                result: answer.result,
                error: answer.error || null,
                busy: !finished,
              },
        );
        if (!finished) timer.current = window.setTimeout(() => poll(jobId), 1000);
      })
      .catch((reason: unknown) => {
        setState((current) => ({
          ...current,
          busy: false,
          error: reason instanceof ApiError ? reason.message : String(reason),
        }));
      });
  }, []);

  const request = useCallback(
    async (body: Record<string, unknown>) => {
      stop();
      setState({ jobId: null, status: "queued", result: null, error: null, busy: true });
      try {
        const created = await api.command<{ job_id: string }>(command, body);
        setState((current) => ({ ...current, jobId: created.job_id }));
        poll(created.job_id);
      } catch (reason) {
        setState({
          jobId: null,
          status: null,
          result: null,
          busy: false,
          error: reason instanceof ApiError ? reason.message : String(reason),
        });
      }
    },
    [command, poll],
  );

  return { state, request };
}

export function useModels() {
  const [models, setModels] = useState<ModelView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    void api
      .query<ModelView[]>("models")
      .then(setModels)
      .catch((reason: unknown) => setError(reason instanceof Error ? reason.message : String(reason)));
  }, []);
  return { models, error };
}

export function initialModel(models: ModelView[] | null): string {
  const wanted = new URLSearchParams(window.location.search).get("model");
  if (!models || models.length === 0) return "";
  return models.some((model) => model.model_id === wanted) ? (wanted as string) : models[0]!.model_id;
}

/** A query-string parameter of the current page (links can open a specific replay). */
export function urlParam(name: string): string | null {
  return new URLSearchParams(window.location.search).get(name);
}
