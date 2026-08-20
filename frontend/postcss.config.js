import tailwindcss from "@tailwindcss/postcss";

import semanticAlphaFallback from "./scripts/postcss-semantic-alpha-fallback.mjs";

export default {
  plugins: [tailwindcss(), semanticAlphaFallback()],
};
