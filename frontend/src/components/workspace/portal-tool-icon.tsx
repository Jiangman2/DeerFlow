import { PORTAL_ICON_ARTWORK } from "@/core/portal-icon-artwork";
import { PORTAL_TOOLS } from "@/core/portal-links";

const outboundIcons = new Set<string>(PORTAL_TOOLS.map((tool) => tool.id));

export function PortalToolIcon({
  id,
}: {
  id: keyof typeof PORTAL_ICON_ARTWORK;
}) {
  return (
    <span className="size-full" data-testid={`portal-icon-${id}`}>
      {/* Pre-encoded local bitmap: bypass the network and Next's optimizer. */}
      <img
        src={PORTAL_ICON_ARTWORK[id]}
        alt=""
        aria-hidden="true"
        width={144}
        height={144}
        loading="eager"
        className="size-full"
        data-testid={
          outboundIcons.has(id) ? "external-navigation-marker" : undefined
        }
      />
    </span>
  );
}
