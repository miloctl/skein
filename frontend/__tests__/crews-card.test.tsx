import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

/** A crew card has the same three states every list surface here has, and two
 *  more things to get right: the edit controls belong to a steward or an
 *  administrator with a key (routes/api.py::_crew_steward), and a failed write
 *  must not clear what the reader typed. */

type Answer = "pending" | "fail" | (() => unknown);
const mode: { answer: Answer; write: "ok" | "fail" } = {
  answer: "pending",
  write: "ok",
};
const writes: { path: string; init?: RequestInit }[] = [];

vi.mock("@/lib/api", async (importOriginal) => {
  const real = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...real,
    api: (path: string, init?: RequestInit) => {
      if (init?.method && init.method !== "GET") {
        writes.push({ path, init });
        return mode.write === "ok"
          ? Promise.resolve({})
          : Promise.reject(new Error("a crew named 'Platform' already exists"));
      }
      return mode.answer === "pending"
        ? new Promise(() => {}) // never settles: the card stays mid-load
        : mode.answer === "fail"
          ? Promise.reject(new Error("crew service exploded"))
          : Promise.resolve(mode.answer());
    },
    // getUser is deliberately NOT what the card reads — the server-resolved
    // name arrives as a prop. A mock that made them agree would hide that.
    getUser: () => "someone-else",
  };
});

import { CrewsCard } from "@/components/crews-card";

const CREW = {
  id: 1,
  name: "Platform",
  summary: "",
  active: 1,
  members: [
    { person: "ava", role: "steward" },
    { person: "bo", role: "member" },
  ],
};

const card = (props: Partial<{ strong: boolean; me: string; admin: boolean }> = {}) => (
  <CrewsCard strong me="ava" admin={false} {...props} />
);

beforeEach(() => {
  mode.answer = "pending";
  mode.write = "ok";
  writes.length = 0;
});

describe("CrewsCard", () => {
  it("claims nothing while the request is still open", () => {
    mode.answer = "pending";
    render(card());
    // "No crews yet" before the answer is a claim the card cannot make
    expect(screen.queryByText(/no crews yet/i)).toBeNull();
    expect(screen.getByText(/loading/i)).toBeTruthy();
  });

  it("names the card, not the page, when the load fails", () => {
    mode.answer = "fail";
    render(card());
    return waitFor(() => {
      expect(screen.getByText(/cannot load crews/i)).toBeTruthy();
      expect(screen.queryByText(/could not load this page/i)).toBeNull();
      expect(screen.queryByText(/no crews yet/i)).toBeNull();
    });
  });

  it("offers edit controls to a steward, an administrator, and nobody else", async () => {
    mode.answer = () => [CREW];

    const steward = render(card({ me: "ava" }));
    await waitFor(() => expect(screen.getByText("Platform")).toBeTruthy());
    expect(screen.getByLabelText("Add someone to Platform")).toBeTruthy();
    steward.unmount();

    // a plain member is not a steward, even with a key
    const member = render(card({ me: "bo" }));
    await waitFor(() => expect(screen.getByText("Platform")).toBeTruthy());
    expect(screen.queryByLabelText("Add someone to Platform")).toBeNull();
    member.unmount();

    // an administrator can repair a crew they are not in — the server allows
    // it, and hiding it strands a crew whose only steward left
    const boss = render(card({ me: "boss", admin: true }));
    await waitFor(() => expect(screen.getByText("Platform")).toBeTruthy());
    expect(screen.getByLabelText("Add someone to Platform")).toBeTruthy();
    boss.unmount();

    // no key: no controls at all
    render(card({ strong: false, me: "ava" }));
    await waitFor(() => expect(screen.getByText("Platform")).toBeTruthy());
    expect(screen.queryByLabelText("Add someone to Platform")).toBeNull();
    expect(screen.queryByLabelText("New crew name")).toBeNull();
  });

  it("keeps what the reader typed when the write is refused", async () => {
    mode.answer = () => [CREW];
    mode.write = "fail";
    render(card());
    await waitFor(() => expect(screen.getByText("Platform")).toBeTruthy());

    const input = screen.getByLabelText("New crew name") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Platform" } });
    fireEvent.click(screen.getByText("Create crew"));

    // the refusal names the clash, and the name is still there to correct
    await waitFor(() => expect(input.value).toBe("Platform"));
    mode.write = "ok";
  });

  it("counts members in sentence form", async () => {
    mode.answer = () => [{ ...CREW, members: [{ person: "ava", role: "steward" }] }];
    render(card());
    await waitFor(() => expect(screen.getByText(/1 member$/)).toBeTruthy());
  });

  it("states the existing and future access granted by membership", async () => {
    mode.answer = () => [CREW];
    render(card());
    const input = await screen.findByLabelText("Add someone to Platform");
    const form = input.closest("form")!;
    const disclosure = document.getElementById(
      form.getAttribute("aria-describedby")!,
    );
    expect(disclosure?.textContent).toMatch(
      /can read existing and future work visible to Platform/i,
    );
  });

  it("confirms an existing member role change instead of reporting an add", async () => {
    mode.answer = () => [CREW];
    render(card());
    const person = await screen.findByLabelText("Add someone to Platform");
    fireEvent.change(person, { target: { value: "bo" } });
    fireEvent.change(screen.getByLabelText("Role in Platform"), {
      target: { value: "steward" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add to Platform" }));

    const consequence = screen.getByText(
      "Change bo from member to steward? bo will be able to add and remove members and change roles.",
    );
    const confirm = screen.getByRole("button", { name: "Change bo to steward" });
    expect(confirm.getAttribute("aria-describedby")).toBe(consequence.id);
    expect(writes).toHaveLength(0);

    fireEvent.click(confirm);
    await waitFor(() =>
      expect(writes).toContainEqual({
        path: "/api/crews/1/members",
        init: expect.objectContaining({
          method: "POST",
          body: JSON.stringify({
            person: "bo",
            role: "steward",
            expected_role: "member",
          }),
        }),
      }),
    );
  });

  it("states member-removal loss as a complete described sentence", async () => {
    mode.answer = () => [CREW];
    render(card());
    fireEvent.click(
      await screen.findByRole("button", {
        name: "Remove bo from Platform",
      }),
    );

    const confirm = screen.getByRole("button", {
      name: "Confirm: remove bo from Platform",
    });
    const consequence = document.getElementById(
      confirm.getAttribute("aria-describedby")!,
    );
    expect(consequence?.textContent).toBe(
      "After removal, bo loses access to crew work they did not write.",
    );
    expect(screen.getByRole("button", { name: "Cancel removal" })).toBeTruthy();
  });

  it("cancels member removal with Escape and returns focus without writing", async () => {
    mode.answer = () => [CREW];
    render(card());
    const trigger = await screen.findByRole("button", {
      name: "Remove bo from Platform",
    });
    fireEvent.click(trigger);

    const confirm = screen.getByRole("button", {
      name: "Confirm: remove bo from Platform",
    });
    expect(document.activeElement).toBe(confirm);
    fireEvent.keyDown(confirm, { key: "Escape" });

    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("button", {
          name: "Remove bo from Platform",
        }),
      ),
    );
    expect(
      screen.queryByRole("button", {
        name: "Confirm: remove bo from Platform",
      }),
    ).toBeNull();
    expect(writes).toHaveLength(0);
  });
});
