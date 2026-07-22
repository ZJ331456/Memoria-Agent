import * as React from "react"
import { MessageScroller as Primitive } from "@shadcn/react/message-scroller"
import { ArrowDownIcon } from "lucide-react"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

export const MessageScrollerProvider=(props:React.ComponentProps<typeof Primitive.Provider>)=><Primitive.Provider {...props}/>
export const MessageScroller=({className,...props}:React.ComponentProps<typeof Primitive.Root>)=><Primitive.Root data-slot="message-scroller" className={cn("relative flex size-full min-h-0 flex-col overflow-hidden",className)} {...props}/>
export const MessageScrollerViewport=({className,...props}:React.ComponentProps<typeof Primitive.Viewport>)=><Primitive.Viewport data-slot="message-scroller-viewport" className={cn("size-full min-h-0 min-w-0 overflow-y-auto overscroll-contain",className)} {...props}/>
export const MessageScrollerContent=({className,...props}:React.ComponentProps<typeof Primitive.Content>)=><Primitive.Content data-slot="message-scroller-content" className={cn("flex min-h-full flex-col gap-6",className)} {...props}/>
export const MessageScrollerItem=({className,scrollAnchor=false,...props}:React.ComponentProps<typeof Primitive.Item>)=><Primitive.Item data-slot="message-scroller-item" scrollAnchor={scrollAnchor} className={cn("min-w-0 shrink-0",className)} {...props}/>
export function MessageScrollerButton({className,...props}:React.ComponentProps<typeof Primitive.Button>){return <Primitive.Button data-slot="message-scroller-button" direction="end" className={cn("absolute bottom-4 left-1/2 -translate-x-1/2 data-[active=false]:hidden",className)} render={<Button variant="outline" size="icon"/>} {...props}><ArrowDownIcon/><span className="sr-only">滚动到最新消息</span></Primitive.Button>}

