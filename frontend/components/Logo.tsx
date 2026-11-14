/** HarvestLens mark: a lens with crop rows running through it. */
export default function Logo({ light = false, size = 22 }: { light?: boolean; size?: number }) {
  const fg = light ? "#fff" : "#17171c";
  const bg = light ? "#10281d" : "#fff";
  return (
    <span className="inline-flex items-center gap-2" style={{ color: fg }}>
      <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
        <circle cx="12" cy="12" r="12" fill={fg} />
        <g stroke={bg} strokeWidth="1.7" strokeLinecap="round" fill="none">
          <path d="M3.5 15.5C8 13 16 13 20.5 15.5" />
          <path d="M5.5 11C9 9.2 15 9.2 18.5 11" />
          <path d="M8.5 6.8C10.5 6 13.5 6 15.5 6.8" />
        </g>
      </svg>
      <span className="font-display text-[19px] font-medium tracking-[-0.02em]">HarvestLens</span>
    </span>
  );
}
