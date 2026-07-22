import type { SVGProps } from 'react'
type IconProps=SVGProps<SVGSVGElement>&{size?:number}
const Icon=({children,size,...props}:IconProps)=><svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" {...props}>{children}</svg>
export const Brain=(p:IconProps)=><Icon {...p}><path d="M9.5 4A3.5 3.5 0 0 0 6 7.5v.2A3.5 3.5 0 0 0 4 11v1a3 3 0 0 0 2 2.8V16a4 4 0 0 0 4 4h2V4Z"/><path d="M14.5 4A3.5 3.5 0 0 1 18 7.5v.2a3.5 3.5 0 0 1 2 3.3v1a3 3 0 0 1-2 2.8V16a4 4 0 0 1-4 4h-2V4Z"/></Icon>
export const MessageSquarePlus=(p:IconProps)=><Icon {...p}><path d="M21 15a4 4 0 0 1-4 4H8l-5 3V7a4 4 0 0 1 4-4h6"/><path d="M19 2v6M16 5h6"/></Icon>
export const Database=(p:IconProps)=><Icon {...p}><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></Icon>
export const LayoutDashboard=(p:IconProps)=><Icon {...p}><rect x="3" y="3" width="7" height="9"/><rect x="14" y="3" width="7" height="5"/><rect x="14" y="12" width="7" height="9"/><rect x="3" y="16" width="7" height="5"/></Icon>
export const Send=(p:IconProps)=><Icon {...p}><path d="m22 2-7 20-4-9-9-4Z M22 2 11 13"/></Icon>
export const Trash2=(p:IconProps)=><Icon {...p}><path d="M3 6h18M8 6V4h8v2M19 6l-1 15H6L5 6M10 11v6M14 11v6"/></Icon>
export const Sparkles=(p:IconProps)=><Icon {...p}><path d="m12 3 1.4 3.6L17 8l-3.6 1.4L12 13l-1.4-3.6L7 8l3.6-1.4ZM5 15l1 2 2 1-2 1-1 2-1-2-2-1 2-1Z"/></Icon>
export const Server=(p:IconProps)=><Icon {...p}><rect x="3" y="4" width="18" height="6" rx="2"/><rect x="3" y="14" width="18" height="6" rx="2"/><path d="M7 7h.01M7 17h.01"/></Icon>
export const Search=(p:IconProps)=><Icon {...p}><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></Icon>
export const Plus=(p:IconProps)=><Icon {...p}><path d="M12 5v14M5 12h14"/></Icon>
export const LoaderCircle=(p:IconProps)=><Icon {...p}><path d="M21 12a9 9 0 1 1-6.2-8.6"/></Icon>
