; ModuleID = "program"
target triple = "x86_64-unknown-linux-gnu"
target datalayout = ""

declare i32 @"printf"(i8* %".1", ...)

define i32 @"main"()
{
entry:
  %"x" = alloca i32
  store i32 0, i32* %"x"
  %"y" = alloca i32
  store i32 10, i32* %"y"
  %"add" = add i32 2, 5
  %"z" = alloca i32
  store i32 %"add", i32* %"z"
  %"x.1" = load i32, i32* %"x"
  %"add.1" = add i32 %"x.1", 10
  %"t" = alloca i32
  store i32 %"add.1", i32* %"t"
  %"t.1" = load i32, i32* %"t"
  %"z.1" = load i32, i32* %"z"
  %"mul" = mul i32 %"t.1", %"z.1"
  store i32 %"mul", i32* %"t"
  %"t.2" = load i32, i32* %"t"
  %".7" = getelementptr inbounds [29 x i8], [29 x i8]* @".fmt", i64 0, i64 0
  %".8" = call i32 (i8*, ...) @"printf"(i8* %".7", i32 %"t.2")
  ret i32 0
}

@".fmt" = constant [29 x i8] c"Program exit with result %d\0a\00"