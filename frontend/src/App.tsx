import { useQuery } from "@tanstack/react-query";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

type Health = { status: string; version: string };

async function fetchHealth(): Promise<Health> {
  const response = await fetch("/health");
  if (!response.ok) {
    throw new Error("The local API is unavailable.");
  }
  return (await response.json()) as Health;
}

export default function App() {
  const health = useQuery({ queryKey: ["health"], queryFn: fetchHealth });
  const apiStatus = health.isPending
    ? "Checking local API…"
    : health.isError
      ? "Local API unavailable"
      : `API ${health.data.status} · v${health.data.version}`;

  return (
    <main className="grid min-h-screen place-items-center bg-background p-6 text-foreground">
      <Card className="w-full max-w-md border-border bg-card">
        <CardHeader>
          <CardTitle className="text-3xl">OpenClip</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-muted-foreground" role="status">
            {apiStatus}
          </p>
        </CardContent>
      </Card>
    </main>
  );
}
