# Reflection

## 1. The most useful features or idioms covered by the course
For me, the most useful idea was that compiler is a five-step chain and each step depends on the link before it. 
Lexer makes the tokens, parser makes the AST tree, semabtic pass checks it and codegen turns it into llvmIR.
When I understood this, compiler stoped looking like some magic for me.
Also symbol table as a stack of frames was usefull, because after that scopes and shadowing finally made sense.
I really liked print_struct, it just walks over the field table and prints every field, so new structure prints without any new code.
It looks like a small thing but I think its very smart.

## 2. Features that were not covered properly
I think threads and coroutines were covered too fast.
I understand that threads share heap but not stack, but not much more then that.
Also the C side, like ABI and why wrong declaration gives garbage and not an error, was explained but I never saw it actually break.
I think one real crash with a wrong extern would help more then slides.

## 3. Examples related to languages and compilers that surprised me most
Biggest surprise for me was that llvm IR dont have loops at all.
Loop is just blocks and one jump back, the back edge and thats it.
Second surprise were lambdas, i always thought they are something special, but compiler just rewrites them into struct with a field for every capture and a call function.
The python example where [lambda: i for i in range(3)] gives 2, 2, 2 and not 0, 1, 2 also surprised me a lot.
And it was funny that on linux llc needs -relocation-model =pic, because without it linker just refuses to build the program.

## 4. Concepts (idioms) or approaches that are still not clear enough
The fixpoint for mutating member functions is still a bit confusing for me.
I get the idea that one method can call another one, so you repeat until nothing changes, but I could not write it from zero without looking. Phi is also only half clear. I know that mem2reg makes phi for us from allocas, but if i need to write phi by hand i would probably, most definetly, mess it up.
Same with why only i32 and i64 can go into C.
I understand its about C agreeing on size and how value is passed, but full ABI rules for structs is still are a black box for me.

## 5. Other ideas and suggestions related to the course content
GROUP PROJECTS! I like those, no ai, quick thinking, remembering all the stuff.
Also, in my humble opinion, it would be good to have one practice where we break things on purpose.
For example, call printf with wrong declaration or link a struct with different layout and see what happens.
Also step by step demo of optimizer, one step a time, one punch at a time, one round at a time would make "repeat until nothing changes" much easier to understand.
Overall the course was helpfull, but building my own language from zero is the part i will actually remember.
