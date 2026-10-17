--  Splits an OFX body into tags and character data. It serves OFX 1.x SGML,
--  where element end tags are optional, and OFX 2.x XML alike; the grammar in
--  Ofx.Parser decides which tags are elements.

package Ofx.Lexer with SPARK_Mode is

   Max_Name : constant := 64;

   type Token_Kind is
     (Start_Tag,     --  <NAME>
      Empty_Tag,     --  <NAME/>
      End_Tag,       --  </NAME>
      Text_Run,      --  character data up to the next '<'
      Comment,       --  <!-- ... -->
      End_Of_Input,
      Malformed);

   type Token is record
      Kind  : Token_Kind := End_Of_Input;
      Name  : Span := No_Span;      --  a tag's name
      Value : Span := No_Span;      --  Text_Run data without edge white space
      Next  : Positive := 1;        --  the position after the token
   end record;

   procedure Next (Text : String; From : Positive; Result : out Token)
   with
     Pre  => Text'First = 1 and then Text'Last <= Max_Input
             and then From <= Text'Last + 1,
     Post => Result.Next in From .. Text'Last + 1
             and then (if Result.Kind in Start_Tag | Empty_Tag | End_Tag | Text_Run | Comment
                       then Result.Next > From)
             and then (if Result.Kind in Start_Tag | Empty_Tag | End_Tag
                       then not Is_Empty (Result.Name)
                            and then Length (Result.Name) <= Max_Name)
             and then Within (Result.Name, Text)
             and then Within (Result.Value, Text);

end Ofx.Lexer;
