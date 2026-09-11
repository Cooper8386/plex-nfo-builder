import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";
import { api } from "../lib/api";
import SettingsView from "./SettingsView";

it("saves the optional OMDb key without echoing it and clears discarded edits", async () => {
  localStorage.setItem("pnb.settings.section", "providers");
  vi.spyOn(api.settings, "get")
    .mockResolvedValueOnce({ omdb_api_key_configured: false })
    .mockResolvedValue({ omdb_api_key_configured: true });
  const save = vi.spyOn(api.settings, "set").mockResolvedValue({ ok: true });
  const user = userEvent.setup();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SettingsView />
    </QueryClientProvider>,
  );

  const key = await screen.findByLabelText("OMDb API key");
  expect(key).toHaveAttribute("type", "password");
  await user.type(key, "private-key");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(save).toHaveBeenCalledWith({ omdb_api_key: "private-key" }));
  const configured = await screen.findByLabelText("OMDb API key (configured)");
  expect(configured).toHaveValue("");
  await user.type(configured, "discarded-key");
  await user.click(screen.getByRole("button", { name: "Discard changes" }));
  expect(configured).toHaveValue("");
  expect(screen.getByRole("button", { name: "Save changes" })).toBeDisabled();
});
