import { defineRailway, github, preserve, project, service } from "railway/iac";

export default defineRailway(() => {
  const api = service("handoff-api", {
    source: github("yusufdirdis/handoff---SmartGlove", {
      branch: "feat/deploy",
      rootDirectory: "backend",
    }),
    start: "uvicorn app.main:app --host 0.0.0.0 --port $PORT --no-proxy-headers",
    healthcheck: "/api/health",
    replicas: 1,
    env: {
      HANDOFF_INSTANCE: "railway",
      HANDOFF_CLOUD: "1",
      HANDOFF_LIVE_POSITIONS: "0",
      SUPABASE_DB_URL: preserve(),
      HANDOFF_PASSCODE: preserve(),
      HANDOFF_CORS_ORIGINS: preserve(),
    },
  });

  return project("handoff", { resources: [api] });
});
