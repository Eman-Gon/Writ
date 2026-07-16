export default function ControlLoading() {
  return (
    <div className="mx-auto w-full max-w-7xl px-5 py-8 sm:px-8 lg:px-10 lg:py-10">
      <div className="h-3 w-28 animate-pulse rounded-full bg-muted" />
      <div className="mt-5 h-10 w-full max-w-lg animate-pulse rounded-lg bg-muted" />
      <div className="mt-3 h-5 w-full max-w-2xl animate-pulse rounded bg-muted" />
      <div className="mt-10 grid gap-4 lg:grid-cols-2">
        <div className="h-72 animate-pulse rounded-xl border border-border bg-card" />
        <div className="h-72 animate-pulse rounded-xl border border-border bg-card" />
      </div>
    </div>
  );
}
