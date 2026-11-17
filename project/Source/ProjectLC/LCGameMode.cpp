#include "LCGameMode.h"
#include "LCCharacter.h"

ALCGameMode::ALCGameMode()
{
    DefaultPawnClass = ALCCharacter::StaticClass();
}
