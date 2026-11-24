export default function CaseTabLoading() {
  return (
    <div aria-busy="true" aria-live="polite" className="space-y-4">
      <div className="h-5 w-44 rounded-[4px] bg-sunken" />
      <div className="panel space-y-3 p-5">
        <div className="h-4 w-2/3 rounded-[4px] bg-sunken" />
        <div className="h-4 w-1/2 rounded-[4px] bg-sunken" />
        <div className="h-24 w-full rounded-[4px] bg-sunken" />
      </div>
      <span className="sr-only">Sekme yükleniyor</span>
    </div>
  );
}
