import { describe, expect, it, afterEach } from "vitest";
import { mainContentRoot } from "./dom";

describe("mainContentRoot", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("prefers <main>", () => {
    document.body.innerHTML = "<nav>x</nav><main id='m'><article>a</article><article>b</article></main>";
    expect(mainContentRoot().id).toBe("m");
  });

  it("uses a lone <article> as the content", () => {
    document.body.innerHTML = "<header>h</header><article id='a'>text</article>";
    expect(mainContentRoot().id).toBe("a");
  });

  it("reads the whole page when articles are listing cards", () => {
    document.body.innerHTML =
      "<article>Book 1 £51.77</article><article>Book 2 £13.99</article><article>Book 3 £17.46</article>";
    expect(mainContentRoot()).toBe(document.body);
  });
});
