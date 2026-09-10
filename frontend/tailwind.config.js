/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  safelist: ["status-none", "status-partial", "status-complete", "status-stale", "status-foreign", "status-mixed"],
  theme: { extend: { colors: {
    slate: { 950: '#101114', 900: '#181a1f', 800: '#272b32', 700: '#3b414c', 600: '#677080', 500: '#8b93a2', 400: '#a3aab6', 300: '#bdc3cc', 200: '#d7dce3', 100: '#eceef2', 50: '#f8f9fb' },
    indigo: { 950: '#2b2315', 900: '#44331b', 800: '#624921', 700: '#86612c', 600: '#936820', 500: '#b88832', 400: '#e8ae45', 300: '#edc379', 200: '#f2d59e', 100: '#f8e7c8', 50: '#fdf6e8' },
  } } },
  plugins: [],
};
