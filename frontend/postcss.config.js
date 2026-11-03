import { fileURLToPath } from "node:url";

export default {
  plugins: {
    "@tailwindcss/postcss": {},
    [fileURLToPath(new URL("./scripts/postcss-semantic-alpha-fallback-webpack.cjs", import.meta.url))]: {},
  },
};
