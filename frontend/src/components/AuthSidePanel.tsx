import Tag from './ui/Tag'

// The mockup's "one account, one shelf" panel, shared by Login.tsx and
// Register.tsx (Phase 5.5) — identical in both, so its copy stays in one
// place rather than duplicated and risking drift.
const STEPS = [
  'Add a repo by URL or zip.',
  "Read the guide it writes — diagrams, trade-offs, citations.",
  'Take the quiz to find out what you actually absorbed.',
]

function AuthSidePanel() {
  return (
    <div className="relative overflow-hidden rounded-[36px] bg-organic-surface p-9">
      <div className="absolute -top-12 -right-10 size-[170px] rounded-full bg-organic-accent-2-200" />
      <div className="relative">
        <Tag variant="accent-2">One account, one shelf</Tag>
        <p className="mt-4 mb-5.5 max-w-[340px] text-[17px] leading-relaxed">
          Strata Learn is built for a single reader. Your repos, guides and quiz history all sit behind this one
          login.
        </p>
        <div className="flex flex-col gap-3.5">
          {STEPS.map((step, index) => (
            <div key={step} className="flex items-start gap-3">
              <span className="grid size-6 flex-none place-items-center rounded-full bg-organic-accent-2-300 text-[11px] font-bold text-organic-accent-2-800">
                {index + 1}
              </span>
              <span className="text-sm leading-normal">{step}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

export default AuthSidePanel
