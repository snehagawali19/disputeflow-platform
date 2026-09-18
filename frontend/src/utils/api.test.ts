import { describe, expect, it } from "vitest";
import { apiBase } from "./api";

describe("api helpers", () => {
  it("uses same-origin /api", () => {
    expect(apiBase()).toBe("/api");
  });
});
