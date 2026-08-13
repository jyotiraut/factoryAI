export function Pager({
  total,
  limit,
  offset,
  onOffsetChange,
}: {
  total: number;
  limit: number;
  offset: number;
  onOffsetChange: (offset: number) => void;
}) {
  const page = Math.floor(offset / limit) + 1;
  const pageCount = Math.max(1, Math.ceil(total / limit));

  return (
    <div className="row" style={{ justifyContent: "space-between" }}>
      <span className="muted">
        {total === 0 ? "No results" : `Page ${page} of ${pageCount} — ${total} total`}
      </span>
      <div className="row">
        <button disabled={offset === 0} onClick={() => onOffsetChange(Math.max(0, offset - limit))}>
          Previous
        </button>
        <button
          disabled={offset + limit >= total}
          onClick={() => onOffsetChange(offset + limit)}
        >
          Next
        </button>
      </div>
    </div>
  );
}
