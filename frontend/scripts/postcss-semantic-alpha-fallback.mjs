const semanticColors = new Set([
  "accent",
  "accent-foreground",
  "background",
  "border",
  "card",
  "card-foreground",
  "chart-1",
  "chart-2",
  "chart-3",
  "chart-4",
  "chart-5",
  "destructive",
  "foreground",
  "input",
  "muted",
  "muted-foreground",
  "popover",
  "popover-foreground",
  "primary",
  "primary-foreground",
  "ring",
  "secondary",
  "secondary-foreground",
  "sidebar",
  "sidebar-accent",
  "sidebar-accent-foreground",
  "sidebar-border",
  "sidebar-foreground",
  "sidebar-primary",
  "sidebar-primary-foreground",
  "sidebar-ring",
]);

const semanticColorMix =
  /color-mix\(\s*in\s+oklab\s*,\s*var\(\s*--(?:color-)?([a-z0-9-]+)\s*\)\s+([0-9.]+)%\s*,\s*transparent\s*\)/gi;

const semanticSlashAlphaClass = new RegExp(
  `-(?:${[...semanticColors].sort((a, b) => b.length - a.length).join("|")})\\\\/([0-9.]+)`,
  "i",
);

function legacyColor(color, percentage) {
  const alpha = Number(percentage) / 100;
  return `rgba(var(--legacy-${color}-rgb), calc(var(--legacy-${color}-opacity, 1) * ${alpha}))`;
}

export function addSemanticAlphaFallback(value) {
  let changed = false;
  const fallback = value.replace(
    semanticColorMix,
    (match, color, percentage) => {
      if (!semanticColors.has(color)) {
        return match;
      }
      changed = true;
      return legacyColor(color, percentage);
    },
  );
  return changed ? fallback : null;
}

function addTailwindClassFallback(declaration) {
  if (declaration.parent?.type !== "rule") {
    return false;
  }

  const selectorDetails = declaration.parent.selector
    .split(",")
    .map((selector) => {
      const classMatch = selector.match(semanticSlashAlphaClass);
      if (!classMatch) {
        return null;
      }
      const fullMatch = classMatch[0];
      const color = fullMatch.slice(1, fullMatch.lastIndexOf("\\/"));
      const percentage = classMatch[1];
      return semanticColors.has(color) && percentage
        ? { color, percentage, selector }
        : null;
    });
  const matchingDetails = selectorDetails.filter(Boolean);
  if (matchingDetails.length === 0) {
    return false;
  }

  const { color, percentage } = matchingDetails[0];

  const semanticVariable = new RegExp(
    `var\\(\\s*--(?:color-)?${color}\\s*\\)`,
    "gi",
  );
  if (
    declaration.value.includes("color-mix(") ||
    !semanticVariable.test(declaration.value)
  ) {
    return false;
  }

  semanticVariable.lastIndex = 0;
  const fallbackValue = declaration.value.replace(
    semanticVariable,
    legacyColor(color, percentage),
  );
  const compatibleSelectors = matchingDetails
    .filter(
      (detail) => detail.color === color && detail.percentage === percentage,
    )
    .map((detail) => detail.selector);

  if (compatibleSelectors.length === selectorDetails.length) {
    declaration.value = fallbackValue;
    return true;
  }

  const overrideRule = declaration.parent.clone({
    selector: compatibleSelectors.join(","),
  });
  overrideRule.removeAll();
  overrideRule.append(declaration.clone({ value: fallbackValue }));
  declaration.parent.after(overrideRule);
  return true;
}

export default function semanticAlphaFallback() {
  return {
    postcssPlugin: "deerflow-semantic-alpha-fallback",
    Declaration(declaration) {
      if (addTailwindClassFallback(declaration)) {
        return;
      }
      const fallback = addSemanticAlphaFallback(declaration.value);
      if (fallback && fallback !== declaration.value) {
        declaration.cloneBefore({ value: fallback });
      }
    },
  };
}

semanticAlphaFallback.postcss = true;
