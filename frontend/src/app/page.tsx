import { redirect } from "next/navigation";

// The overview page arrives in phase 4; until then the news page is the home page.
export default function Home() {
  redirect("/news");
}
